import json
import logging
import random
import re
import time
from typing import Optional, Dict, Any, List, Iterator, Generator
from urllib.parse import urlparse

import httpx
from openai import OpenAI, APIError, APIConnectionError, RateLimitError, AuthenticationError

from backend.config import settings
from backend.services.llm_settings_store import LLMSettingsStore

logger = logging.getLogger(__name__)


def _compute_retry_delay(attempt: int) -> float:
    """
    计算带 jitter 的指数退避延迟（秒）。

    公式：delay = base * 2^attempt + uniform(0, base)
    其中 base=1.0, attempt 从 0 开始（0/1/2 对应第 1/2/3 次重试）。

    加 jitter 的目的：
      - 多个客户端同时失败时，jitter 打破"齐步走"重试，避免雪崩
      - 指数退避给后端 API 喘息时间

    实际延迟区间：
      attempt=0: [1.0, 2.0)s
      attempt=1: [2.0, 3.0)s
      attempt=2: [4.0, 5.0)s
    """
    base = 1.0
    exponential = base * (2 ** attempt)
    jitter = random.uniform(0, base)
    return exponential + jitter


class LLMService:
    """LLM服务封装类 - 支持多模型切换 + 运行时热更新

    配置来源（优先级从高到低）：
      1. usercontext/llm_settings.json （由设置页写入）
      2. 环境变量（向后兼容老配置，API_KEY / *_BASE_URL / *_MODEL）

    行为：
      - 每次 __init__ 都会从 LLMSettingsStore 重新读取配置
        ——保证设置页改动后，下一次对话/角色创建即可生效，**无需重启后端**。
      - 内部维护 self._loaded_at 时间戳；可在外部调用 reload_config() 强制重读。
    """

    _MAX_RETRIES = 3
    _RETRY_BASE_DELAY = 1.0  # 退避基数（秒），与 _compute_retry_delay 配合使用
    # OpenAI 客户端超时（秒）：
    #   - 流式首字响应一般 < 10s，超时 15s 后自动 fallback
    #   - 非流式总响应可能在 15-30s，超时 25s
    # 这两个常量控制单次 API 请求的最大等待时间。
    _TIMEOUT = 25.0
    _STREAM_TIMEOUT = 15.0  # 流式首字超时（httpx 层）
    # 连接池调优：
    #   现象：连续 streaming 调用时，第 1 轮成功，第 2 轮立即 Connection error。
    #   根因：Agnes API 端点对 keepalive 长连接处理有问题——客户端复用上一次的连接
    #         时服务端要么拒绝要么已经 RST，但 client 不知道，继续用死连接发请求。
    #   修复：完全禁用 keepalive（max_keepalive_connections=0），让每次请求都新建 TCP 连接。
    #         代价是每个请求多 ~30ms TCP 三次握手 + TLS 握手，但能彻底解决 Connection error。
    #   验证：纯 openai SDK 跑 5 轮连续 streaming 也会复现同样模式；
    #         keepalive_expiry 设短值也不够（Agnes 似乎立即断），必须彻底关掉。
    # [P1-1 修复] keepalive 按 provider 区分：
    #   - Agnes 端点对 keepalive 长连接有问题（注释见 _HTTPX_LIMITS 原始说明）
    #   - 其他 provider（qwen / openai / deepseek / zhipu / ollama）支持 keepalive，
    #     关掉会多 200-500ms TCP+TLS 握手，对 TTFT 影响显著
    # Agnes 用 0，其他用 5
    _HTTPX_LIMITS_AGN = httpx.Limits(
        max_keepalive_connections=0,
        max_connections=10,
    )
    _HTTPX_LIMITS = httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10,
    )

    # [P1] 进程级 provider 缓存：provider_id -> {
    #   "client": OpenAI, "model": str, "base_url": str, "api_key": str,
    #   "http_client": httpx.Client, "provider": str, "loaded_at": float
    # }
    # 共享 httpx 连接池，按需 lazy-init，避免每个 LLMService() 都新建客户端。
    # 类级而非实例级，跨 LLMService 实例共享（任意模块 import 后都走同一份 client）。
    _PROVIDER_CACHE: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        # 主 provider = active_provider（向后兼容旧代码直接读 self.client / self.model）
        self.reload_config()

    def reload_config(self, provider_id: Optional[str] = None) -> None:
        """
        重新加载配置，并初始化对应的 OpenAI client。

        调用场景：
          - __init__ 内部（默认）
          - 设置页 PUT 成功后由 main.py 显式调用
          - 切换 active_provider 后调用
          - 任务路由变更后调用（清空缓存，下次按 task 重新解析）

        Args:
            provider_id: 不传 → 清空全部缓存并重载主 provider (=active_provider)。
                          传 → 只重载指定 provider。
        """
        if provider_id is not None:
            # 单 provider 增量重载
            self._init_provider(provider_id)
            # 如果是主 provider，把缓存状态同步到 self.xxx 兼容旧代码
            if provider_id == self._active_provider_id:
                self._sync_primary_state(self._PROVIDER_CACHE[provider_id])
            return

        # 全量重载：清空缓存 → 重新初始化主 provider
        self._close_all_cached_clients()
        self._PROVIDER_CACHE.clear()
        self._init_primary_provider()

    def _init_primary_provider(self) -> None:
        """初始化主 provider = active_provider。状态同步到 self.xxx。"""
        store = LLMSettingsStore()
        self._active_provider_id = store.get_active_provider_id()
        self._task_routing = store.get_task_routing()
        prov = self._init_provider(self._active_provider_id)
        self._sync_primary_state(prov)

    def _init_provider(self, provider_id: str) -> Dict[str, Any]:
        """
        初始化并缓存指定 provider。已缓存直接返回。

        Raises:
            ValueError: api_key / base_url / model 缺失或非法
        """
        if provider_id in self._PROVIDER_CACHE:
            return self._PROVIDER_CACHE[provider_id]
        store = LLMSettingsStore()
        # get_provider_with_env_fallback 自动从环境变量补齐缺失字段
        cfg = store.get_provider_with_env_fallback(provider_id)

        api_key = (cfg.get("api_key", "") or "").strip()
        base_url = cfg.get("base_url", "")
        model = cfg.get("model", "")

        if not api_key and provider_id != "ollama":
            raise ValueError(
                f"provider={provider_id} 的 API Key 为空。"
                f"请在设置页填写，或在 .env 中设置 {provider_id.upper()}_API_KEY"
            )
        if not base_url:
            raise ValueError(f"provider={provider_id} 的 base_url 为空")
        if not model:
            raise ValueError(f"provider={provider_id} 的 model 为空")

        self._validate_base_url(base_url)

        # 显式 httpx.Client + 按 provider 选 keepalive limits：
        #   - Agnes 端点对 keepalive 长连接有问题 → 强制每次新建 TCP 连接
        #   - 其他 provider（qwen/openai/...）支持 keepalive → 复用连接降低 TTFT
        limits = self._HTTPX_LIMITS_AGN if provider_id == "agnes" else self._HTTPX_LIMITS
        http_client = httpx.Client(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            limits=limits,
        )
        client = OpenAI(
            api_key=api_key if provider_id != "ollama" else "ollama",
            base_url=base_url,
            http_client=http_client,
        )
        prov = {
            "provider": provider_id,
            "client": client,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "http_client": http_client,
            "loaded_at": time.time(),
        }
        self._PROVIDER_CACHE[provider_id] = prov
        logger.info(
            "LLMService 初始化 provider=%s, model=%s, base_url=%s",
            provider_id, model, base_url,
        )
        return prov

    def _sync_primary_state(self, prov: Dict[str, Any]) -> None:
        """把缓存中的 provider 状态同步到 self.xxx（兼容旧代码读 self.client / self.model）"""
        self.provider = prov["provider"]
        self.client = prov["client"]
        self.model = prov["model"]
        self.base_url = prov["base_url"]
        self._api_key = prov["api_key"]
        self._loaded_at = prov["loaded_at"]

    def _resolve_task_provider(self, task: Optional[str]) -> Dict[str, Any]:
        """
        [P1] 按 task 解析出目标 provider 的缓存 dict。
        - task=None → 主 provider（active_provider）
        - task 存在于 routing → 用 routing 指定的 provider
        - task 不在 routing → 回退到主 provider
        路由未命中时仍走主 provider，保证向后兼容。
        """
        if not task:
            return self._PROVIDER_CACHE[self._active_provider_id]
        provider_id = self._task_routing.get(task, self._active_provider_id)
        return self._init_provider(provider_id)

    def _close_all_cached_clients(self) -> None:
        """关闭所有缓存的 httpx client 底层连接。"""
        for prov in self._PROVIDER_CACHE.values():
            http_client = prov.get("http_client")
            if http_client is not None:
                try:
                    http_client.close()
                except Exception:
                    pass

    def _reset_client(self, provider_id: Optional[str] = None) -> None:
        """
        重建指定 provider 的 OpenAI client（替换 httpx.Client）。

        使用场景：
          - 连续 connection error 时，旧 httpx 客户端的连接池里可能全是死连接
          - 直接重建 client 比逐条关闭再重连更彻底，避免复用半死的 socket
        调用方：call_with_messages_stream / _call_with_retry 在
                APIConnectionError 首次出现后立即调用一次。

        Args:
            provider_id: 不传则重置主 provider（向后兼容旧代码）。
        """
        if provider_id is None:
            provider_id = self.provider
        prov = self._PROVIDER_CACHE.get(provider_id)
        if prov is None:
            return
        # 关闭旧 http_client
        try:
            old_http = prov.get("http_client")
            if old_http is not None and hasattr(old_http, "close"):
                try:
                    old_http.close()
                except Exception:
                    pass
        except Exception:
            pass
        # 重建
        limits = self._HTTPX_LIMITS_AGN if provider_id == "agnes" else self._HTTPX_LIMITS
        new_http = httpx.Client(
            timeout=httpx.Timeout(connect=10, read=15, write=10, pool=10),
            limits=limits,
        )
        prov["http_client"] = new_http
        prov["client"] = OpenAI(
            api_key=prov["api_key"] if provider_id != "ollama" else "ollama",
            base_url=prov["base_url"],
            http_client=new_http,
        )
        # 同步到主状态（如果重置的是主 provider）
        if provider_id == self.provider:
            self.client = prov["client"]
        logger.warning("LLMService 已重建 client (provider=%s)", provider_id)

    @staticmethod
    def _try_env_fallback(provider_id: str, suffix: str) -> Optional[str]:
        """
        从环境变量回退读取（仅当 JSON 文件里没值时使用）。
        兼容 .env 中形如 AGNES_API_KEY / DEEPSEEK_API_KEY / QWEN_BASE_URL 等命名。
        保留为 @staticmethod 以便其他场景使用；reload_config 主路径已统一走 store。
        """
        import os
        env_name = f"{provider_id.upper()}_{suffix}"
        return os.environ.get(env_name) or None

    def _validate_base_url(self, base_url: str) -> None:
        """校验 base_url 格式合法性"""
        if not base_url:
            raise ValueError("base_url 不能为空")

        try:
            parsed = urlparse(base_url)
            if not parsed.scheme or parsed.scheme not in ("http", "https"):
                raise ValueError("base_url 必须以 http:// 或 https:// 开头")
            if not parsed.netloc:
                raise ValueError("base_url 缺少有效的域名或IP地址")
        except ValueError as e:
            raise ValueError(f"base_url 格式错误: {e}")

    def call(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        response_format: Optional[dict] = None,
        task: Optional[str] = None,
    ) -> str:
        """
        调用LLM（单轮：system + user）

        Args:
            prompt: 用户prompt
            system_prompt: 系统prompt（可选）
            temperature: 温度参数（0-1）
            max_tokens: 最大token数
            response_format: 响应格式约束（可选，例如 {"type": "json_object"}）。
                           默认 None 即不约束格式，由调用方按需传入。
            task: 任务名（可选，None=主 provider）。用于按任务路由 provider，
                  例如 task="creation" 走 agnes、task="chat" 走 qwen。
                  取值参考 LLMSettingsStore.DEFAULT_TASK_ROUTING。

        Returns:
            LLM的响应文本
        """
        self._validate_call_params(prompt, system_prompt, temperature, max_tokens)

        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        prov = self._resolve_task_provider(task)
        kwargs = dict(
            model=prov["model"],
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format is not None:
            kwargs["response_format"] = response_format

        if task:
            logger.debug("call task=%s -> provider=%s model=%s", task, prov["provider"], prov["model"])
        return self._call_with_retry_for(kwargs, prov)

    def call_with_messages(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1000,
        response_format: Optional[dict] = None,
        task: Optional[str] = None,
    ) -> str:
        """
        使用已组装好的多轮 messages 数组调用 LLM。

        与 call() 的区别：
          - call()          只能传单条 prompt，自动拼成 [system?, user]
          - call_with_messages() 接受调用方已组装好的完整消息列表，
                                  支持多轮对话上下文（system + 历史 user/assistant + 当前 user）

        Args:
            messages: 已组装的消息数组，每个元素必须是 {"role": ..., "content": ...}
                      至少包含 1 条消息；role 必须是 system/user/assistant 之一
            temperature: 温度参数（0-2）
            max_tokens: 最大token数（1-32000）
            response_format: 响应格式约束（可选）
            task: 任务名（按 task_routing 路由 provider，参考 call() 文档）

        Returns:
            LLM的响应文本

        Raises:
            ValueError: 参数非法时
        """
        # --- 校验 messages ---
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages 必须是非空列表")

        valid_roles = {"system", "user", "assistant"}
        validated: List[Dict[str, str]] = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise ValueError(f"messages[{idx}] 必须是字典")
            role = msg.get("role")
            content = msg.get("content")
            if role not in valid_roles:
                raise ValueError(
                    f"messages[{idx}].role 必须是 {valid_roles} 之一，得到 {role!r}"
                )
            if not isinstance(content, str):
                raise ValueError(f"messages[{idx}].content 必须是字符串")
            validated.append({"role": role, "content": content})

        # --- 校验其他参数 ---
        if not isinstance(temperature, (int, float)):
            raise ValueError("temperature 必须是数值")
        if temperature < 0 or temperature > 2:
            raise ValueError("temperature 必须在 [0, 2] 范围内")
        if not isinstance(max_tokens, int):
            raise ValueError("max_tokens 必须是整数")
        if max_tokens < 1 or max_tokens > 32000:
            raise ValueError("max_tokens 必须在 [1, 32000] 范围内")

        prov = self._resolve_task_provider(task)
        kwargs = dict(
            model=prov["model"],
            messages=validated,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format is not None:
            kwargs["response_format"] = response_format

        logger.debug(
            "call_with_messages task=%s provider=%s: total=%d, history_turns=%d",
            task, prov["provider"], len(validated),
            sum(1 for m in validated if m["role"] in ("user", "assistant")) // 2,
        )
        return self._call_with_retry_for(kwargs, prov)

    def call_with_messages_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1000,
        response_format: Optional[dict] = None,
        task: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """
        流式调用 LLM，逐 chunk 产出文本增量。

        与 call_with_messages 的区别：
          - 使用 OpenAI SDK 的 stream=True，首个 token 到达即开始返回
          - 调用方拿到的是 generator，每次 yield 一段文本增量（delta）
          - 适合聊天界面实时打字效果，显著降低首字延迟（TTFT）

        Args:
            同 call_with_messages

        Yields:
            str: 每次返回一段文本增量（可能为空字符串，调用方需自行过滤）

        Raises:
            同 call_with_messages（含 ValueError / APIError 等）
        """
        # 复用 call_with_messages 的参数校验逻辑
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages 必须是非空列表")

        valid_roles = {"system", "user", "assistant"}
        validated: List[Dict[str, str]] = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise ValueError(f"messages[{idx}] 必须是字典")
            role = msg.get("role")
            content = msg.get("content")
            if role not in valid_roles:
                raise ValueError(
                    f"messages[{idx}].role 必须是 {valid_roles} 之一，得到 {role!r}"
                )
            if not isinstance(content, str):
                raise ValueError(f"messages[{idx}].content 必须是字符串")
            validated.append({"role": role, "content": content})

        if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
            raise ValueError("temperature 必须在 [0, 2] 范围内")
        if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 32000:
            raise ValueError("max_tokens 必须在 [1, 32000] 范围内")

        prov = self._resolve_task_provider(task)
        kwargs = dict(
            model=prov["model"],
            messages=validated,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        if response_format is not None:
            kwargs["response_format"] = response_format

        logger.debug(
            "call_with_messages_stream task=%s provider=%s: total=%d, history_turns=%d",
            task, prov["provider"], len(validated),
            sum(1 for m in validated if m["role"] in ("user", "assistant")) // 2,
        )

        # 流式调用不做重试 —— 部分内容已发送给用户后重试会导致内容跳跃
        # 仅捕获可重试异常并在首个 token 之前重试一次
        # 注意：在生成器中，try 块内赋值的变量在 except 块中可能被 Python 视为"未赋值"
        # 所以用 sentinel object 而非布尔变量
        _NOT_YIELDED = object()
        first_chunk_yielded = _NOT_YIELDED
        last_exception: Optional[Exception] = None
        client_reset_done = False
        for attempt in range(self._MAX_RETRIES):
            try:
                stream = prov["client"].chat.completions.create(**kwargs)
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content:
                        first_chunk_yielded = True
                        yield content
                # 正常结束
                return
            except AuthenticationError as e:
                logger.error(f"LLM认证失败: {str(e)[:200]}")
                raise
            except (RateLimitError, APIConnectionError) as e:
                # 仅在尚未发出任何 chunk 时重试
                if first_chunk_yielded is _NOT_YIELDED and attempt < self._MAX_RETRIES - 1:
                    delay = _compute_retry_delay(attempt)
                    # 第一次出现 connection error 时立刻重建 client —— 旧连接池里的死连接不能复用
                    is_conn_err = isinstance(e, APIConnectionError)
                    if is_conn_err and not client_reset_done:
                        try:
                            self._reset_client(prov["provider"])
                        except Exception as reset_err:
                            logger.warning("重建 client 失败: %s", reset_err)
                        client_reset_done = True
                    logger.warning(
                        f"LLM流式连接失败(首字前), task={task} provider={prov['provider']}, attempt={attempt+1}/{self._MAX_RETRIES}, delay={delay:.2f}s: {str(e)[:200]}"
                    )
                    time.sleep(delay)
                    last_exception = e
                    continue
                raise
            except APIError as e:
                if first_chunk_yielded is _NOT_YIELDED and attempt < self._MAX_RETRIES - 1:
                    delay = _compute_retry_delay(attempt)
                    logger.warning(
                        f"LLM流式API错误(首字前), task={task} provider={prov['provider']}, attempt={attempt+1}/{self._MAX_RETRIES}, delay={delay:.2f}s: {str(e)[:200]}"
                    )
                    time.sleep(delay)
                    last_exception = e
                    continue
                raise
            except Exception as e:
                logger.error(f"LLM流式调用未知错误: {str(e)[:200]}")
                raise

        if last_exception:
            raise last_exception

    def _call_with_retry(self, kwargs: Dict[str, Any]) -> str:
        """
        [兼容旧代码] 等价于 _call_with_retry_for(kwargs, self._PROVIDER_CACHE[self.provider])。
        旧代码若直接调 self._call_with_retry(kwargs) 仍走主 provider。
        """
        return self._call_with_retry_for(kwargs, self._PROVIDER_CACHE[self.provider])

    def _call_with_retry_for(self, kwargs: Dict[str, Any], prov: Dict[str, Any]) -> str:
        """
        执行带重试的 LLM 调用（被 call / call_with_messages 共用）。

        抽离此方法的动机：
          - call 与 call_with_messages 的重试/异常处理逻辑完全一致
          - 集中一处便于未来统一调整重试策略（如指数退避、熔断等）
          - 接受 provider dict 参数，支持路由到非主 provider

        Args:
            kwargs: 传给 openai client 的参数（model / messages / temperature / ...）
            prov: 来自 _resolve_task_provider(task) 的 provider 缓存 dict
        """
        client = prov["client"]
        provider_id = prov["provider"]
        last_exception = None
        for attempt in range(self._MAX_RETRIES):
            try:
                response = client.chat.completions.create(**kwargs)
                return self._extract_content(response)

            except AuthenticationError as e:
                logger.error(f"LLM认证失败(provider={provider_id}): {str(e)[:200]}")
                raise

            except RateLimitError as e:
                delay = _compute_retry_delay(attempt)
                logger.warning(f"LLM限流(provider={provider_id}): attempt={attempt+1}/{self._MAX_RETRIES}, delay={delay:.2f}s, {str(e)[:200]}")
                if attempt < self._MAX_RETRIES - 1:
                    time.sleep(delay)
                    continue
                last_exception = e

            except APIConnectionError as e:
                delay = _compute_retry_delay(attempt)
                logger.warning(f"LLM连接失败(provider={provider_id}): attempt={attempt+1}/{self._MAX_RETRIES}, delay={delay:.2f}s, {str(e)[:200]}")
                if attempt < self._MAX_RETRIES - 1:
                    time.sleep(delay)
                    continue
                last_exception = e

            except APIError as e:
                delay = _compute_retry_delay(attempt)
                logger.error(f"LLM API错误(provider={provider_id}): attempt={attempt+1}/{self._MAX_RETRIES}, delay={delay:.2f}s, {str(e)[:200]}")
                if attempt < self._MAX_RETRIES - 1:
                    time.sleep(delay)
                    continue
                last_exception = e

            except Exception as e:
                logger.error(f"LLM调用未知错误(provider={provider_id}): {str(e)[:200]}")
                raise

        if last_exception:
            raise last_exception

        # 理论上不会到达这里（每次循环要么 return 要么 continue 要么 raise）
        raise RuntimeError("LLM调用异常结束：未返回结果也未抛出异常")

    def _validate_call_params(
        self,
        prompt: str,
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int
    ) -> None:
        """校验调用参数合法性"""
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt 必须是非空字符串")

        if system_prompt is not None:
            if not isinstance(system_prompt, str):
                raise ValueError("system_prompt 必须是字符串")
            if not system_prompt.strip():
                system_prompt = None

        if not isinstance(temperature, (int, float)):
            raise ValueError("temperature 必须是数值")
        if temperature < 0 or temperature > 2:
            raise ValueError("temperature 必须在 [0, 2] 范围内")

        if not isinstance(max_tokens, int):
            raise ValueError("max_tokens 必须是整数")
        if max_tokens < 1 or max_tokens > 32000:
            raise ValueError("max_tokens 必须在 [1, 32000] 范围内")

    def _extract_content(self, response: Any) -> str:
        """安全提取响应内容"""
        if not response:
            logger.warning("LLM返回空响应")
            return ""

        if not hasattr(response, "choices") or not response.choices:
            logger.warning("LLM返回空choices")
            return ""

        first_choice = response.choices[0]
        if not first_choice:
            logger.warning("LLM第一个choice为空")
            return ""

        message = getattr(first_choice, "message", None)
        if not message:
            logger.warning("LLM响应中message为空")
            return ""

        content = getattr(message, "content", None)
        if content is None:
            logger.warning("LLM响应content为None")
            return ""

        if not isinstance(content, str):
            logger.warning(f"LLM响应content类型异常: {type(content)}")
            try:
                return str(content)
            except Exception:
                return ""

        return content.strip()

    def parse_json_response(self, response: str) -> dict:
        """
        解析LLM的JSON响应

        Args:
            response: LLM返回的字符串

        Returns:
            解析后的字典
        """
        if not response or not isinstance(response, str) or not response.strip():
            raise ValueError("响应为空，无法解析JSON")

        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            pass

        try:
            json_match = re.search(r'\{[^}]*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except (json.JSONDecodeError, re.error):
            pass

        raise ValueError(f"无法解析LLM响应为JSON: {response[:200]}...")

    @staticmethod
    def validate_creation_schema(data: dict) -> dict:
        """
        轻量级 schema 校验：验证 Creation LLM 输出的必要字段与类型。

        校验内容：
        1. 顶层必填字段：name, world_setting, personality, current_state
        2. personality 子字段：optimism, courage, empathy, loyalty,
           intelligence, sociability（要求为 0-100 整数值）
        3. current_state 子字段：location, activity, mood

        Args:
            data: 解析后的字典

        Returns:
            校验通过后的字典（personality 数值已转为 int）

        Raises:
            ValueError: 缺少必要字段或类型错误时
        """
        if not isinstance(data, dict):
            raise ValueError("数据必须是字典")

        required_top = ["name", "world_setting", "personality", "current_state"]
        for field in required_top:
            if field not in data or data[field] is None:
                raise ValueError(f"LLM响应缺少必填字段: '{field}'")

        personality = data["personality"]
        if not isinstance(personality, dict):
            raise ValueError("'personality' 必须是 JSON 对象")

        personality_fields = [
            "optimism", "courage", "empathy",
            "loyalty", "intelligence", "sociability"
        ]
        for field in personality_fields:
            if field not in personality:
                raise ValueError(f"personality 缺少字段: '{field}'")
            try:
                val = int(personality[field])
                if val < 0 or val > 100:
                    val = max(0, min(100, val))
                personality[field] = val
            except (ValueError, TypeError):
                personality[field] = 50

        current_state = data["current_state"]
        if not isinstance(current_state, dict):
            raise ValueError("'current_state' 必须是 JSON 对象")

        for field in ["location", "activity", "mood"]:
            if field not in current_state:
                current_state[field] = ""
            elif not isinstance(current_state[field], str):
                current_state[field] = str(current_state[field])

        return data

    @staticmethod
    def validate_director_schema(data: dict) -> dict:
        """
        轻量级 schema 校验：验证 Director LLM 输出的必要字段与类型。

        校验内容：
        1. 顶层必填字段：emotion, focus_memories, goal, style（均为 string 或 list）
        2. focus_memories 必须是 list[str] 类型，最多 3 条
        3. 所有字符串字段不能为空

        设计考量：
          - 不做 emotion 枚举约束，给 LLM 自由发挥空间（"悲喜交加"、"怅然若失"等复合情绪）
          - focus_memories 截断到 3 条，作为 prompt 工程之外的兜底保护

        Args:
            data: 解析后的字典

        Returns:
            校验通过后的字典

        Raises:
            ValueError: 缺少必要字段或类型错误时
        """
        if not isinstance(data, dict):
            raise ValueError("数据必须是字典")

        defaults = {
            "emotion": "neutral",
            "focus_memories": [],
            "goal": "继续对话",
            "style": "natural"
        }

        for field, default in defaults.items():
            if field not in data or data[field] is None:
                data[field] = default

        emotion = data["emotion"]
        if not isinstance(emotion, str) or not emotion.strip():
            data["emotion"] = "neutral"
        else:
            data["emotion"] = emotion.strip()

        focus_memories = data["focus_memories"]
        if not isinstance(focus_memories, list):
            data["focus_memories"] = []
        else:
            data["focus_memories"] = [
                str(m).strip() for m in focus_memories if m and str(m).strip()
            ][:3]

        goal = data["goal"]
        if not isinstance(goal, str) or not goal.strip():
            data["goal"] = "继续对话"
        else:
            data["goal"] = goal.strip()

        style = data["style"]
        if not isinstance(style, str) or not style.strip():
            data["style"] = "natural"
        else:
            data["style"] = style.strip()

        return data

    @staticmethod
    def validate_actor_schema(data: dict) -> dict:
        """
        轻量级 schema 校验：验证 Actor LLM 输出的必要字段与类型。

        校验内容：
        1. 顶层必填字段：action, expression, speech（均为非空字符串）
        2. speech 做最小长度校验（>= 1 字符）以防止空回复

        设计考量：
          - Actor 输出结构简单（3 个字符串），校验逻辑轻薄
          - speech 不做最大长度限制，给 LLM 充分的表达空间
          - 不做 OOC 检测（超出角色设定的回复），这是 prompt 层面的责任

        Args:
            data: 解析后的字典

        Returns:
            校验通过后的字典

        Raises:
            ValueError: 缺少必要字段或类型错误时
        """
        if not isinstance(data, dict):
            raise ValueError("数据必须是字典")

        defaults = {
            "action": "stand",
            "expression": "neutral",
            "speech": "..."
        }

        for field, default in defaults.items():
            if field not in data or data[field] is None:
                data[field] = default

        for field in ["action", "expression", "speech"]:
            value = data[field]
            if not isinstance(value, str):
                data[field] = str(value) if value else defaults[field]
            if not data[field].strip():
                data[field] = defaults[field]

        return data

    @staticmethod
    def validate_growth_schema(data: dict) -> dict:
        """
        轻量级 schema 校验：验证 Growth LLM 输出的必要字段与类型。

        校验内容：
        1. 顶层必填字段：personality_delta (dict), new_memories (list), event_summary (str)
        2. personality_delta 子字段：6 个人格维度，值域 [-30, 30]
        3. new_memories 数组元素：每条含 content(str) + importance(int 1-10)，最多 3 条
        4. event_summary 为非空字符串

        设计考量：
          - delta 范围限制在 [-30, 30]：防止 LLM 一次输出极端变化（如 optimism 直接 -90）
          - new_memories 截断到 3 条：prompt 已要求 ≤3 条，但 schema 层二次兜底
          - 不对事件摘要做最大长度限制：给 LLM 充分的叙事空间

        Args:
            data: 解析后的字典

        Returns:
            校验通过后的字典（personality_delta 数值已转为 int）

        Raises:
            ValueError: 缺少必要字段或类型/范围错误时
        """
        if not isinstance(data, dict):
            raise ValueError("数据必须是字典")

        if "personality_delta" not in data or data["personality_delta"] is None:
            data["personality_delta"] = {}
        personality_delta = data["personality_delta"]
        if not isinstance(personality_delta, dict):
            data["personality_delta"] = {}
            personality_delta = {}

        personality_fields = [
            "optimism", "courage", "empathy",
            "loyalty", "intelligence", "sociability"
        ]
        for field in personality_fields:
            if field not in personality_delta:
                personality_delta[field] = 0
            else:
                try:
                    val = int(personality_delta[field])
                    val = max(-30, min(30, val))
                    personality_delta[field] = val
                except (ValueError, TypeError):
                    personality_delta[field] = 0

        if "new_memories" not in data or data["new_memories"] is None:
            data["new_memories"] = []
        new_memories = data["new_memories"]
        if not isinstance(new_memories, list):
            data["new_memories"] = []
            new_memories = []

        validated_memories = []
        for mem in new_memories:
            if not isinstance(mem, dict):
                continue
            content = mem.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            try:
                importance = int(mem.get("importance", 5))
                importance = max(1, min(10, importance))
            except (ValueError, TypeError):
                importance = 5
            validated_memories.append({
                "content": content.strip(),
                "importance": importance
            })

        data["new_memories"] = validated_memories[:3]

        if "event_summary" not in data or data["event_summary"] is None:
            data["event_summary"] = "角色经历了一次成长"
        else:
            event_summary = data["event_summary"]
            if not isinstance(event_summary, str) or not event_summary.strip():
                data["event_summary"] = "角色经历了一次成长"

        return data
