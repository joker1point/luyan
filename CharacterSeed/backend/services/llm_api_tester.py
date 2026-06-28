"""
LLM API 连通性测试服务（参考 https://github.com/joker1point/web-tools 的 react-vite 实现）

设计目标：
  把"一键拉取 /v1/models 列表"和"流式延迟测试（TTFT + 总延迟）"能力
  从前端 React 组件移植到 FastAPI 后端，前端 Vue Web 通过简单 HTTP 调
  用即可复用相同能力。这样：
    1. 测试逻辑集中在后端，便于复用与单元测试
    2. 浏览器不再需要直接持有 API Key（安全）
    3. 复用现有的 LLMSettingsStore，无需重复维护 provider 配置

能力对应 web-tools 的功能：
  - fetch_models()        ← react-vite/src/App.jsx 的 fetchModels()
  - test_stream_latency() ← react-vite/src/App.jsx 的 testLatency()（含 Anthropic 适配）
  - probe_request()       ← 新增的"原始响应头/体"调试接口
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from backend.services.llm_settings_store import LLMSettingsStore

logger = logging.getLogger(__name__)


# ============================================================
# Provider 探测与请求头构造
# ============================================================

# Anthropic API 域名列表（用于自动切换认证方式）
_ANTHROPIC_HOSTS = ("anthropic.com",)


def _is_anthropic_base(base_url: str) -> bool:
    """判定 base_url 是否指向 Anthropic 官方域名"""
    try:
        host = urlparse(base_url).netloc.lower()
    except ValueError:
        return False
    return any(h in host for h in _ANTHROPIC_HOSTS)


def _join_url(base_url: str, suffix: str) -> str:
    """
    拼接 base_url 与子路径，自动处理 base_url 末尾的 /v1 与 suffix 开头的 /v1 重复问题。

    Examples:
        _join_url("https://api.openai.com", "/v1/models")
            -> "https://api.openai.com/v1/models"
        _join_url("https://apihub.agnes-ai.com/v1", "/v1/models")
            -> "https://apihub.agnes-ai.com/v1/models"  (不会变 /v1/v1/models)
        _join_url("https://api.openai.com/v1/", "/v1/chat/completions")
            -> "https://api.openai.com/v1/chat/completions"
    """
    base = (base_url or "").rstrip("/")
    sfx = "/" + suffix.lstrip("/")
    # 避免 base_url 已带 /v1，suffix 又以 /v1 开头时拼出 /v1/v1/...
    if sfx.startswith("/v1/") and base.endswith("/v1"):
        sfx = sfx[len("/v1"):]
    return base + sfx


def _build_headers(api_key: str, base_url: str) -> Dict[str, str]:
    """
    根据 base_url 自动选择认证方式：
      - Anthropic 域名：x-api-key + anthropic-version
      - 其它（OpenAI 兼容）：Authorization: Bearer
    与 web-tools 的 buildHeaders() 行为一致。
    """
    headers = {"Content-Type": "application/json"}
    if _is_anthropic_base(base_url):
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _is_masked_key(s: str) -> bool:
    """检测是否是脱敏 key（含 ****）。来自前端的 masked 串应被静默忽略，不应覆盖 store 里的真 key。"""
    return isinstance(s, str) and '****' in s


def _resolve_config(
    provider_id: Optional[str] = None,
    override_api_key: Optional[str] = None,
    override_base_url: Optional[str] = None,
    override_model: Optional[str] = None,
) -> Tuple[str, Dict[str, str]]:
    """
    解析"使用哪个 provider 的哪份配置"：
      - 不传 provider → 用当前激活 provider
      - 传 provider   → 用指定 provider
      - override_*  → 临时覆盖（仅本函数返回的 dict，不写盘）

    Returns:
        (provider_id, {"api_key", "base_url", "model"})
    """
    store = LLMSettingsStore()
    pid = provider_id or store.get_active_provider_id()
    cfg = store.get_provider_with_env_fallback(pid)
    if override_api_key and not _is_masked_key(override_api_key):
        cfg["api_key"] = override_api_key
    if override_base_url is not None:
        cfg["base_url"] = override_base_url
    if override_model is not None:
        cfg["model"] = override_model
    return pid, cfg


# ============================================================
# 能力 1：拉取 /v1/models 列表
# ============================================================

def fetch_models(
    provider_id: Optional[str] = None,
    override_api_key: Optional[str] = None,
    override_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    调用 provider 的 GET /v1/models 接口，返回可用模型列表与请求耗时。

    Returns:
        {
            "provider_id": str,
            "base_url": str,
            "models": [{"id": str, "owned_by": str, "object": str}, ...],
            "duration_ms": int,
            "raw_count": int,
        }
    Raises:
        ValueError: 配置缺失
        RuntimeError: HTTP 错误（带状态码 + 响应体片段）
    """
    pid, cfg = _resolve_config(provider_id, override_api_key, override_base_url)
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "").rstrip("/")

    if not base_url:
        raise ValueError("base_url 为空")
    if not api_key and pid != "ollama":
        raise ValueError(f"provider={pid} 的 API Key 为空，请先在设置页填写")

    url = _join_url(base_url, "/v1/models")
    start = time.time()
    res = requests.get(
        url,
        headers=_build_headers(api_key, base_url),
        timeout=20,
    )
    duration_ms = int((time.time() - start) * 1000)

    if not res.ok:
        body = (res.text or "")[:300]
        raise RuntimeError(f"HTTP {res.status_code}: {body or res.reason}")

    try:
        data = res.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(f"返回非 JSON: {res.text[:300]}") from e

    raw_models = data.get("data") or []
    models: List[Dict[str, str]] = []
    for m in raw_models:
        if isinstance(m, dict):
            models.append({
                "id": str(m.get("id", "")),
                "owned_by": str(m.get("owned_by", "") or ""),
                "object": str(m.get("object", "model") or "model"),
            })
        elif isinstance(m, str):
            models.append({"id": m, "owned_by": "", "object": "model"})

    return {
        "provider_id": pid,
        "base_url": base_url,
        "models": models,
        "duration_ms": duration_ms,
        "raw_count": len(models),
    }


# ============================================================
# 能力 2：流式延迟测试（TTFT + 总延迟）
# ============================================================

def _safe_message(msg: Optional[str], max_len: int = 2000, default: str = "Hi") -> str:
    """截断过长的测试消息，提供安全默认值"""
    if msg is None:
        return default
    s = str(msg)
    return s[:max_len] if s else default


def _build_chat_body(
    model: str,
    message: str,
    max_tokens: int,
    is_anthropic: bool,
) -> Dict[str, Any]:
    """构造 chat completions（或 Anthropic messages）请求体"""
    safe_max = max(1, min(int(max_tokens or 16), 2048))
    if is_anthropic:
        return {
            "model": model or "claude-sonnet-4-20250514",
            "max_tokens": safe_max,
            "stream": True,
            "messages": [{"role": "user", "content": message}],
        }
    return {
        "model": model or "gpt-4o-mini",
        "max_tokens": safe_max,
        "stream": True,
        "messages": [{"role": "user", "content": message}],
    }


def test_stream_latency(
    provider_id: Optional[str] = None,
    override_api_key: Optional[str] = None,
    override_base_url: Optional[str] = None,
    override_model: Optional[str] = None,
    test_message: Optional[str] = "Hi",
    max_tokens: int = 16,
) -> Dict[str, Any]:
    """
    流式延迟测试：发送 stream=true 的 chat 请求，测量
      - status: HTTP 状态码
      - ttft_ms: 首字节时间（Time To First Token，毫秒）
      - total_ms: 完整响应时间
      - content: 累计内容（前 200 字符）
      - chunks: 收到的 SSE 块数（仅 OpenAI 协议）
      - error: 错误信息（失败时）

    实现要点（与 web-tools 的 testLatency 行为一致）：
      1. 增量解析 SSE，遇到 finish_reason / message_stop 即 reader.cancel()
         优雅结束，避免读取多余的尾部字节
      2. 失败时也记录 totalDuration，便于排错
    """
    pid, cfg = _resolve_config(
        provider_id, override_api_key, override_base_url, override_model,
    )
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "").rstrip("/")
    model = cfg.get("model", "")

    if not base_url:
        return {
            "provider_id": pid,
            "model": model,
            "status": 0,
            "ttft_ms": None,
            "total_ms": None,
            "content": "",
            "chunks": 0,
            "error": "base_url 为空",
        }
    if not api_key and pid != "ollama":
        return {
            "provider_id": pid,
            "model": model,
            "status": 0,
            "ttft_ms": None,
            "total_ms": None,
            "content": "",
            "chunks": 0,
            "error": f"provider={pid} 的 API Key 为空",
        }

    is_anthropic = _is_anthropic_base(base_url)
    url = _join_url(base_url, f"/v1/{'messages' if is_anthropic else 'chat/completions'}")
    body = _build_chat_body(model, _safe_message(test_message), max_tokens, is_anthropic)

    start = time.time()
    try:
        res = requests.post(
            url,
            headers=_build_headers(api_key, base_url),
            json=body,
            timeout=60,
            stream=True,
        )
    except requests.Timeout:
        return {
            "provider_id": pid,
            "model": model,
            "status": 0,
            "ttft_ms": None,
            "total_ms": int((time.time() - start) * 1000),
            "content": "",
            "chunks": 0,
            "error": "请求超时（60s）",
        }
    except requests.RequestException as e:
        return {
            "provider_id": pid,
            "model": model,
            "status": 0,
            "ttft_ms": None,
            "total_ms": int((time.time() - start) * 1000),
            "content": "",
            "chunks": 0,
            "error": f"连接失败: {str(e)[:200]}",
        }

    status = res.status_code
    if not res.ok:
        body_text = ""
        try:
            # 在错误场景下 stream=True 也能 .text 拿到全部
            body_text = res.text[:300]
        except Exception:  # pragma: no cover
            pass
        return {
            "provider_id": pid,
            "model": model,
            "status": status,
            "ttft_ms": None,
            "total_ms": int((time.time() - start) * 1000),
            "content": "",
            "chunks": 0,
            "error": f"HTTP {status}: {body_text or res.reason}",
        }

    # 解析 SSE 流
    ttft_ms: Optional[int] = None
    content = ""
    chunks = 0
    finished = False
    buffer = ""

    try:
        for raw_chunk in res.iter_lines(chunk_size=1, decode_unicode=True):
            if not raw_chunk:
                continue
            chunks += 1
            if ttft_ms is None:
                ttft_ms = int((time.time() - start) * 1000)

            buffer += raw_chunk + "\n"
            buffer, line_content, line_finished = _parse_sse_line(
                buffer, is_anthropic,
            )
            if line_content:
                content += line_content
            if line_finished:
                finished = True
                # 尽力读完 buffer 里的剩余行（一次性处理完避免半行残留）
                while "\n" in buffer:
                    buffer, c, f = _parse_sse_line(buffer, is_anthropic)
                    if c:
                        content += c
                    if not f:
                        break
                break
    finally:
        # 显式关闭连接，避免 keep-alive 占用 fd
        try:
            res.close()
        except Exception:  # pragma: no cover
            pass

    total_ms = int((time.time() - start) * 1000)
    if not finished and not content and ttft_ms is None:
        # 流式响应未产出任何内容，可能是模型名错或权限不足
        return {
            "provider_id": pid,
            "model": model,
            "status": status,
            "ttft_ms": None,
            "total_ms": total_ms,
            "content": "",
            "chunks": chunks,
            "error": "流式响应为空（模型名错误 / 权限不足 / 网关截断）",
        }

    return {
        "provider_id": pid,
        "model": model,
        "status": status,
        "ttft_ms": ttft_ms,
        "total_ms": total_ms,
        "content": content[:200],
        "chunks": chunks,
        "error": None,
    }


# 简单的 SSE 行级解析：返回 (剩余 buffer, 本次累积文本, 是否结束)
_SSE_DATA_PREFIX = re.compile(r"^data:\s*")


def _parse_sse_line(buffer: str, is_anthropic: bool) -> Tuple[str, str, bool]:
    """
    解析 SSE buffer 中的完整行。
    与 web-tools 的 parseLines 行为一致：
      - 跳过非 data: 行
      - 解析 JSON 提取 content
      - 遇到 [DONE] / message_stop / finish_reason → finished=True
    """
    out_text = ""
    finished = False
    last_newline = buffer.rfind("\n")
    if last_newline < 0:
        return buffer, out_text, finished
    complete = buffer[:last_newline]
    new_buffer = buffer[last_newline + 1:]
    for line in complete.split("\n"):
        trimmed = line.strip()
        if not trimmed.startswith("data:"):
            continue
        payload = _SSE_DATA_PREFIX.sub("", trimmed)
        if not payload or payload == "[DONE]":
            finished = True
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if is_anthropic:
            t = obj.get("type")
            if t == "content_block_delta":
                delta = obj.get("delta") or {}
                out_text += delta.get("text") or ""
            elif t == "message_stop":
                finished = True
        else:
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            chunk_text = delta.get("content")
            if chunk_text:
                out_text += chunk_text
            if choice.get("finish_reason"):
                finished = True
    return new_buffer, out_text, finished


# ============================================================
# 能力 3：原始请求/响应探针（debug 模式）
# ============================================================

def probe_request(
    provider_id: Optional[str] = None,
    override_api_key: Optional[str] = None,
    override_base_url: Optional[str] = None,
    test_message: Optional[str] = "Hi",
    max_tokens: int = 16,
) -> Dict[str, Any]:
    """
    原始响应探针：发送一次非流式请求，返回完整的 request/response 头/体。
    用于排查 provider 鉴权/路由/协议差异。

    Returns:
        {
            "provider_id", "model", "base_url",
            "request": {"method", "url", "headers" (脱敏), "body"},
            "response": {"status", "headers", "body", "duration_ms"},
            "error": Optional[str],
        }
    """
    pid, cfg = _resolve_config(provider_id, override_api_key, override_base_url)
    api_key = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "").rstrip("/")
    model = cfg.get("model", "")

    is_anthropic = _is_anthropic_base(base_url)
    url = _join_url(base_url, f"/v1/{'messages' if is_anthropic else 'chat/completions'}")
    body = _build_chat_body(model, _safe_message(test_message), max_tokens, is_anthropic)
    # 探针一律用非流式，便于一次性看到完整 body
    body["stream"] = False
    headers = _build_headers(api_key, base_url)
    # 脱敏：Authorization / x-api-key 字段值替换为 "***"
    safe_headers = {
        k: ("***" if k.lower() in ("authorization", "x-api-key") else v)
        for k, v in headers.items()
    }

    start = time.time()
    try:
        res = requests.post(
            url, headers=headers, json=body, timeout=30,
        )
    except requests.RequestException as e:
        return {
            "provider_id": pid,
            "model": model,
            "base_url": base_url,
            "request": {"method": "POST", "url": url, "headers": safe_headers, "body": body},
            "response": {
                "status": 0, "headers": {}, "body": "", "duration_ms": int((time.time() - start) * 1000),
            },
            "error": f"连接失败: {str(e)[:200]}",
        }
    duration_ms = int((time.time() - start) * 1000)

    resp_body_text = res.text or ""
    resp_body_truncated = resp_body_text[:2000]
    resp_body_pretty: Any
    try:
        resp_body_pretty = res.json()
    except json.JSONDecodeError:
        resp_body_pretty = resp_body_truncated

    return {
        "provider_id": pid,
        "model": model,
        "base_url": base_url,
        "request": {
            "method": "POST",
            "url": url,
            "headers": safe_headers,
            "body": body,
        },
        "response": {
            "status": res.status_code,
            "headers": dict(res.headers),
            "body": resp_body_pretty,
            "body_raw": resp_body_truncated,
            "duration_ms": duration_ms,
        },
        "error": None if res.ok else f"HTTP {res.status_code}",
    }
