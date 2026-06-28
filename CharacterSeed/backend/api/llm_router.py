"""
llm_router — LLM 设置 + API 联通测试。

端点（设置）：
  GET  /api/settings/llm                       获取当前 LLM 设置（含所有 provider 脱敏配置）
  GET  /api/settings/llm/providers             列出所有 provider（含展示用元信息）
  PUT  /api/settings/llm                       更新 LLM 设置（provider 切换 / config 改 / 默认参数改）
  POST /api/settings/llm/test                  测试 LLM 连接（不传覆盖 → 当前激活；传覆盖 → 临时构造）

端点（API 测试，参考 web-tools）：
  GET  /api/test/models                        拉取 /v1/models 列表
  POST /api/test/latency                       流式延迟测试（TTFT）
  POST /api/test/probe                         原始请求探针

设计要点：
  - 切换 provider 后立即 reload_all_llm() 热更新全局单例的 LLM 配置
  - api_key 始终脱敏（mask_api_key），不向客户端泄露完整密钥
  - test 接口允许临时覆盖字段（不写盘），便于"改完先试一下"
"""
from __future__ import annotations
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from openai import OpenAI

from backend.schemas import (
    LLMSettingsResponse,
    LLMUpdateRequest,
    LLMTestRequest,
    LLMTestResponse,
    ProviderConfigMasked,
    ModelsListResponse,
    LatencyTestRequest,
    LatencyTestResponse,
    ProbeRequest,
    ProbeResponse,
)
from backend.services.llm_settings_store import (
    LLMSettingsStore,
    PROVIDER_META,
    PROVIDER_DEFAULTS,
)
from backend.services import llm_api_tester
from backend.state import reload_all_llm

logger = logging.getLogger(__name__)
router = APIRouter(tags=["llm"])


def _build_settings_response(store: LLMSettingsStore) -> LLMSettingsResponse:
    """
    组装 LLM 设置的对外响应（api_key 全部脱敏）。

    关键：用 store.get_provider_with_env_fallback() 而非原始 JSON，
    这样 .env 中下沉的 key 也能在设置页显示为"已设置 + 脱敏串"，
    否则前端会误判"未设置"。真 key 仍然不出现在响应里（mask 后再返回）。
    """
    active = store.get_active_provider_id()
    active_cfg = store.get_provider_with_env_fallback(active)
    providers_masked: dict = {}
    for meta in PROVIDER_META:
        pid = meta["id"]
        cfg = store.get_provider_with_env_fallback(pid)
        providers_masked[pid] = ProviderConfigMasked(
            api_key=LLMSettingsStore.mask_api_key(cfg.get("api_key", "")),
            base_url=cfg.get("base_url", ""),
            model=cfg.get("model", ""),
        )
    active_meta = next(
        (m for m in PROVIDER_META if m["id"] == active), {"name": active}
    )
    all_data = store.get_all()
    return LLMSettingsResponse(
        active_provider=active,
        active_provider_name=active_meta["name"],
        config=providers_masked[active],
        default_temperature=float(all_data["default_temperature"]),
        default_max_tokens=int(all_data["default_max_tokens"]),
        providers=providers_masked,
        settings_file_path=LLMSettingsStore.settings_file_path(),
        task_routing=all_data.get("task_routing") or {},
        budget=all_data.get("budget") or {},
        cache=all_data.get("cache") or {},
        logging=all_data.get("logging") or {},
    )


@router.get("/api/settings/llm", response_model=LLMSettingsResponse)
def get_llm_settings():
    """获取当前 LLM 设置（含所有 provider 的脱敏配置）。"""
    return _build_settings_response(LLMSettingsStore())


@router.get("/api/settings/llm/providers")
def list_llm_providers():
    """列出所有支持的 provider（含展示用元信息）。"""
    return {
        "providers": LLMSettingsStore.list_providers_meta(),
        "defaults": PROVIDER_DEFAULTS,
    }


@router.put("/api/settings/llm", response_model=LLMSettingsResponse)
def update_llm_settings(request: LLMUpdateRequest):
    """
    更新 LLM 设置。支持的更新动作（任意组合）：
      - 切换激活 provider:        request.active_provider
      - 修改当前激活 provider 配置: request.active_config
      - 修改默认温度:             request.default_temperature
      - 修改默认 max_tokens:      request.default_max_tokens
    """
    store = LLMSettingsStore()

    # 1. 切换激活 provider
    if request.active_provider:
        if request.active_provider not in PROVIDER_DEFAULTS:
            raise HTTPException(
                status_code=400,
                detail=f"未知 provider: {request.active_provider}",
            )
        store.set_active_provider(request.active_provider)

    # 2. 修改当前激活 provider 的配置
    if request.active_config:
        target = store.get_active_provider_id()
        cfg = request.active_config
        try:
            store.update_provider(
                target,
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=cfg.model,
            )
        except KeyError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # 3. 修改默认参数
    if request.default_temperature is not None or request.default_max_tokens is not None:
        store.update_default_params(
            temperature=request.default_temperature,
            max_tokens=request.default_max_tokens,
        )

    # 4. 修改任务路由
    if request.task_routing is not None:
        store.set_task_routing(request.task_routing)

    # 5. 修改预算控制
    if request.budget is not None:
        store.update_budget(**request.budget.model_dump(exclude_none=True))

    # 6. 修改缓存策略
    if request.cache is not None:
        store.update_cache(**request.cache.model_dump(exclude_none=True))

    # 7. 修改日志监控
    if request.logging is not None:
        store.update_logging_config(**request.logging.model_dump(exclude_none=True))

    logger.info("LLM 设置已更新: active=%s", store.get_active_provider_id())

    # 热更新全局单例
    try:
        reload_all_llm()
    except Exception as e:
        logger.warning("热更新 LLM 单例失败（下次请求将按需重建）: %s", e)
    return _build_settings_response(store)


@router.post("/api/settings/llm/test", response_model=LLMTestResponse)
def test_llm_connection(request: LLMTestRequest):
    """
    测试 LLM 连接。
      1) 不传覆盖 → 用当前激活 provider
      2) 传覆盖字段 → 临时构造（不写盘）
    """
    store = LLMSettingsStore()

    pid = request.provider_id or store.get_active_provider_id()
    if pid not in PROVIDER_DEFAULTS:
        raise HTTPException(status_code=400, detail=f"未知 provider: {pid}")

    if request.provider_id or request.api_key or request.base_url or request.model:
        base_cfg = store.get_provider_with_env_fallback(pid)
        api_key = request.api_key if request.api_key is not None else base_cfg["api_key"]
        base_url = request.base_url if request.base_url is not None else base_cfg["base_url"]
        model = request.model if request.model is not None else base_cfg["model"]
    else:
        cfg = store.get_provider_with_env_fallback(pid)
        api_key, base_url, model = cfg["api_key"], cfg["base_url"], cfg["model"]

    # Ollama 不需要 api_key
    if not api_key and pid != "ollama":
        return LLMTestResponse(
            success=False,
            message=f"API Key 为空，请先在设置中填写 {pid} 的 API Key",
            provider_id=pid,
            model=model,
        )

    test_prompt = request.test_prompt or "你好"
    t0 = time.time()
    try:
        client = OpenAI(api_key=api_key or "ollama", base_url=base_url, timeout=20)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": test_prompt}],
            temperature=0.0,
            max_tokens=80,
        )
        latency_ms = int((time.time() - t0) * 1000)
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return LLMTestResponse(
                success=False,
                message="LLM 返回了空内容（可能模型名错误或权限不足）",
                provider_id=pid,
                model=model,
                latency_ms=latency_ms,
            )
        return LLMTestResponse(
            success=True,
            message=f"连接成功（{latency_ms}ms）",
            provider_id=pid,
            model=model,
            response_text=text[:200],
            latency_ms=latency_ms,
        )
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        logger.warning("LLM 连接测试失败: provider=%s, err=%s", pid, str(e)[:300])
        return LLMTestResponse(
            success=False,
            message=f"连接失败: {str(e)[:200]}",
            provider_id=pid,
            model=model,
            latency_ms=latency_ms,
        )


# ==================== API Test Endpoints ====================

@router.get("/api/test/models", response_model=ModelsListResponse)
def list_models(
    provider_id: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
):
    """拉取 provider 的 /v1/models 列表（含耗时）。"""
    try:
        return llm_api_tester.fetch_models(
            provider_id=provider_id,
            override_api_key=api_key,
            override_base_url=base_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("拉取 models 失败")
        raise HTTPException(status_code=500, detail=f"拉取失败: {str(e)[:200]}")


@router.post("/api/test/latency", response_model=LatencyTestResponse)
def test_latency(request: LatencyTestRequest):
    """流式延迟测试：发送 stream=true 请求，测量 TTFT + 总延迟。"""
    try:
        return llm_api_tester.test_stream_latency(
            provider_id=request.provider_id,
            override_api_key=request.api_key,
            override_base_url=request.base_url,
            override_model=request.model,
            test_message=request.test_message,
            max_tokens=request.max_tokens or 16,
        )
    except Exception as e:
        logger.exception("延迟测试异常")
        return {
            "provider_id": request.provider_id or "",
            "model": request.model or "",
            "status": 0,
            "ttft_ms": None,
            "total_ms": None,
            "content": "",
            "chunks": 0,
            "error": f"测试异常: {str(e)[:200]}",
        }


@router.post("/api/test/probe", response_model=ProbeResponse)
def probe_llm(request: ProbeRequest):
    """原始请求探针：返回完整 request/response 头/体（密钥已脱敏）。"""
    try:
        return llm_api_tester.probe_request(
            provider_id=request.provider_id,
            override_api_key=request.api_key,
            override_base_url=request.base_url,
            test_message=request.test_message,
            max_tokens=request.max_tokens or 16,
        )
    except Exception as e:
        logger.exception("探针异常")
        return {
            "provider_id": request.provider_id or "",
            "model": "",
            "base_url": "",
            "request": {},
            "response": {},
            "error": f"探针异常: {str(e)[:200]}",
        }
