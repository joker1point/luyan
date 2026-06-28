"""
LLM 设置存储（File-based JSON Store）

设计目标：
  - 把"LLM 厂商 / API Key / 模型"等配置从 .env 文件迁出
    改为可视化设置页可改、可持久化（参考 NextChat 的做法）。
  - 存储位置遵循项目规则：usercontext/llm_settings.json
    （usercontext 目录是用户级别数据的存放点）。
  - 原子写：先写 .tmp，再 os.replace，避免写入中途崩溃导致配置损坏。

文件结构：
  {
    "active_provider": "agnes",
    "providers": {...},
    "default_temperature": 0.7,
    "default_max_tokens": 1000,
    "task_routing": {                # [P1] 按任务路由到不同 provider
      "chat": "qwen",                # 实时对话 → Qwen（国内低延迟、TTFT 友好）
      "chat_stream": "qwen",         # 流式对话
      "creation": "agnes",           # 一次性创建任务可慢
      "creation_polish": "agnes",    # 描述润色
      "growth": "agnes",             # 角色成长（后台批）
      "event": "agnes",              # 事件推进
      "time": "agnes",               # 时间推进
      "memory_extraction": "agnes",  # 记忆提取（后台）
      "summary": "agnes"             # 摘要生成（后台）
    }
  }

线程安全：单进程文件读写不加锁（FastAPI 单进程下请求串行 + 短临界区）。
        如未来要部署多 worker，可换 threading.Lock 或外部 Redis。
"""
import json
import logging
import os
import threading
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
# 存放在 <project_root>/usercontext/llm_settings.json
# 不放在 backend/ 下面：避免后端代码改动时误删配置
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SETTINGS_DIR = os.path.join(_PROJECT_ROOT, "usercontext")
_SETTINGS_FILE = os.path.join(_SETTINGS_DIR, "llm_settings.json")

# 同一进程内多次调用 SettingsStore 共享文件锁
_file_lock = threading.Lock()

# 内存缓存：避免每次 _read() 都读磁盘（高频对话场景下显著减少 IO）
# 写入时清空，下次 _read() 重新加载。受 _file_lock 保护。
_cache: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Provider 默认配置（首次启动时写入文件）
# ---------------------------------------------------------------------------
PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "deepseek": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "qwen": {
        "api_key": "",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo",
    },
    "zhipu": {
        "api_key": "",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
    "ollama": {
        # Ollama 是本地服务，不需要 API Key
        "api_key": "",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
    },
    "openai": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "agnes": {
        "api_key": "",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "model": "agnes-1.5-flash",
    },
}

# 用于前端下拉选择时显示的"厂商信息表"
PROVIDER_META: List[Dict[str, str]] = [
    {"id": "deepseek", "name": "DeepSeek",      "needs_key": "true"},
    {"id": "qwen",     "name": "通义千问 (Qwen)", "needs_key": "true"},
    {"id": "zhipu",    "name": "智谱 GLM",       "needs_key": "true"},
    {"id": "ollama",   "name": "Ollama (本地)",  "needs_key": "false"},
    {"id": "openai",   "name": "OpenAI",         "needs_key": "true"},
    {"id": "agnes",    "name": "Agnes AI",       "needs_key": "true"},
]

DEFAULT_ACTIVE = "qwen"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 1000

# [P1] 按任务路由 provider 的默认映射
# 路由原则：
#   - 实时对话（chat / chat_stream）→ Qwen（国内低延迟、中文流式好）
#   - 后台非实时任务 → Agnes（用户主 key，免费额度更大）
# 用户可在 llm_settings.json 的 task_routing 字段覆写
DEFAULT_TASK_ROUTING: Dict[str, str] = {
    "chat": "qwen",
    "chat_stream": "qwen",
    "creation": "agnes",
    "creation_polish": "agnes",
    "growth": "agnes",
    "event": "agnes",
    "time": "agnes",
    "memory_extraction": "agnes",
    "summary": "agnes",
}

# ---------------------------------------------------------------------------
# 成本控制默认配置
# ---------------------------------------------------------------------------
DEFAULT_BUDGET: Dict[str, Any] = {
    "enabled": False,                    # 是否启用预算控制
    "daily_limit_yuan": 10.0,            # 日预算上限（元）
    "monthly_limit_yuan": 200.0,         # 月预算上限（元）
    "single_call_token_limit": 8000,     # 单次调用 token 上限
    "alert_threshold_percent": 80,       # 告警阈值（百分比）
}

# ---------------------------------------------------------------------------
# 缓存策略默认配置
# ---------------------------------------------------------------------------
DEFAULT_CACHE: Dict[str, Any] = {
    "ttl_seconds": 300,                  # 缓存有效期（秒）
    "max_size": 1000,                    # 最大缓存条目数
    "enable_response_cache": True,       # 是否启用响应缓存
    "cache_granularity": "character",    # 缓存粒度: character / session / global
}

# ---------------------------------------------------------------------------
# 日志监控默认配置
# ---------------------------------------------------------------------------
DEFAULT_LOGGING: Dict[str, Any] = {
    "level": "INFO",                     # 日志级别: DEBUG / INFO / WARNING / ERROR
    "record_api_calls": True,            # 是否记录 API 调用详情
    "record_token_usage": True,          # 是否记录 token 使用量
    "max_log_entries": 10000,            # 最大日志条目数
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _default_settings() -> Dict[str, Any]:
    """首次启动时使用的默认配置（含每个 provider 的默认值）"""
    return {
        "active_provider": DEFAULT_ACTIVE,
        "providers": {pid: dict(cfg) for pid, cfg in PROVIDER_DEFAULTS.items()},
        "default_temperature": DEFAULT_TEMPERATURE,
        "default_max_tokens": DEFAULT_MAX_TOKENS,
        "task_routing": dict(DEFAULT_TASK_ROUTING),
        "budget": dict(DEFAULT_BUDGET),
        "cache": dict(DEFAULT_CACHE),
        "logging": dict(DEFAULT_LOGGING),
    }


def _merge_defaults(stored: Dict[str, Any]) -> Dict[str, Any]:
    """
    合并"硬盘上的配置"与"代码中的 PROVIDER_DEFAULTS"。

    目的：当代码新增了一个 provider / 改了默认值时，
    已存在的用户配置文件应自动获得新字段（不覆盖用户已有的 api_key）。

    兜底：stored 中 base_url / model 为空字符串时也回退到默认
    （防止历史脏数据导致 LLMService reload 失败、API key 看似保存但没生效）。

    Returns:
        合并后的完整配置（已保证包含所有 provider 字段）。
    """
    base = _default_settings()
    base["active_provider"] = stored.get("active_provider", DEFAULT_ACTIVE)
    base["default_temperature"] = float(
        stored.get("default_temperature", DEFAULT_TEMPERATURE)
    )
    base["default_max_tokens"] = int(
        stored.get("default_max_tokens", DEFAULT_MAX_TOKENS)
    )
    stored_providers = stored.get("providers") or {}
    for pid, default_cfg in PROVIDER_DEFAULTS.items():
        existing = stored_providers.get(pid) or {}
        # 已有值 → 用 existing；缺失字段 → 用 default
        # 关键：base_url/model 为空字符串/None 都视为缺失
        merged = dict(default_cfg)
        merged.update({k: v for k, v in existing.items()
                       if v not in (None, "")})
        # api_key 允许保留空（用户可能故意清空）
        if pid in stored_providers and "api_key" in stored_providers[pid]:
            merged["api_key"] = stored_providers[pid].get("api_key", "") or ""
        base["providers"][pid] = merged
    # [P1] 合并 task_routing：代码默认 + 用户的覆写
    user_routing = stored.get("task_routing") or {}
    for task, provider_id in user_routing.items():
        if provider_id and provider_id in base["providers"]:
            base["task_routing"][task] = provider_id
        # 非法 provider_id 静默丢弃（不抛错，避免历史脏配置启动失败）
    # 合并 budget / cache / logging：代码默认 + 用户覆写（逐 key 合并）
    for key, defaults in [("budget", DEFAULT_BUDGET), ("cache", DEFAULT_CACHE), ("logging", DEFAULT_LOGGING)]:
        user_block = stored.get(key) or {}
        merged_block = dict(defaults)
        merged_block.update({k: v for k, v in user_block.items() if v is not None})
        base[key] = merged_block
    return base


def _atomic_write(path: str, data: str) -> None:
    """原子写：先写 .tmp 再 os.replace，避免半写状态污染主文件"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())  # 强刷盘，防止 OS 缓存丢数据
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------
class LLMSettingsStore:
    """
    LLM 设置文件存储。

    用法：
        store = LLMSettingsStore()           # 自动加载 / 初始化
        active = store.get_active_provider() # 取得当前激活 provider 的完整配置
        store.set_active_provider("openai")  # 切换
        store.update_provider("openai", api_key="sk-...")  # 修改字段
    """

    def __init__(self) -> None:
        self._ensure_loaded()

    # -------------------- 文件 IO --------------------
    def _ensure_loaded(self) -> None:
        """保证文件存在；不存在则写入默认值。"""
        with _file_lock:
            if not os.path.exists(_SETTINGS_FILE):
                os.makedirs(_SETTINGS_DIR, exist_ok=True)
                _atomic_write(
                    _SETTINGS_FILE,
                    json.dumps(_default_settings(), ensure_ascii=False, indent=2),
                )
                logger.info("初始化 LLM 设置文件: %s", _SETTINGS_FILE)

    def _read(self) -> Dict[str, Any]:
        global _cache
        with _file_lock:
            if _cache is not None:
                return _merge_defaults(_cache)
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = f.read()
        try:
            stored = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            logger.warning("LLM 设置文件损坏，使用默认配置")
            stored = {}
        merged = _merge_defaults(stored)
        with _file_lock:
            _cache = merged
        return merged

    def _write(self, data: Dict[str, Any]) -> None:
        global _cache
        with _file_lock:
            os.makedirs(_SETTINGS_DIR, exist_ok=True)
            _atomic_write(
                _SETTINGS_FILE,
                json.dumps(data, ensure_ascii=False, indent=2),
            )
            _cache = None  # 清缓存，下次 _read() 重新加载

    # -------------------- 对外 API --------------------
    def get_all(self) -> Dict[str, Any]:
        """读取完整配置（包含所有 provider）。"""
        return self._read()

    def get_active_provider_id(self) -> str:
        return self._read()["active_provider"]

    def get_active_provider(self) -> Dict[str, str]:
        """取得当前激活 provider 的 {api_key, base_url, model} 字典。"""
        data = self._read()
        pid = data["active_provider"]
        return dict(data["providers"][pid])

    def get_provider(self, provider_id: str) -> Dict[str, str]:
        data = self._read()
        if provider_id not in data["providers"]:
            raise KeyError(f"未知 provider: {provider_id}")
        return dict(data["providers"][provider_id])

    def get_default_params(self) -> Dict[str, float]:
        data = self._read()
        return {
            "temperature": float(data["default_temperature"]),
            "max_tokens": int(data["default_max_tokens"]),
        }

    def set_active_provider(self, provider_id: str) -> None:
        data = self._read()
        if provider_id not in data["providers"]:
            raise KeyError(f"未知 provider: {provider_id}")
        data["active_provider"] = provider_id
        self._write(data)
        logger.info("切换激活 provider: %s", provider_id)

    def update_provider(
        self,
        provider_id: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        更新指定 provider 的字段（None 表示不修改）。

        关键防护：
          - api_key: None/空串都视为"不修改"（避免前端 masked 串 / 空表单覆盖真 key）
          - base_url / model: 空字符串自动用 PROVIDER_DEFAULTS 兜底
            （避免前端表单编辑失误把空值覆盖进存储，
            导致 LLMService.reload_config() 校验失败、新 key 看似保存却没生效）

        Returns:
            更新后的完整配置。
        """
        data = self._read()
        if provider_id not in data["providers"]:
            raise KeyError(f"未知 provider: {provider_id}")
        cfg = data["providers"][provider_id]

        # [P0#2] 防御：前端可能把后端 mask_api_key 返回的脱敏串（如 "sk-4****d96f"）
        # 当成 api_key 回传。这里的 truthy 检查无法识别脱敏串，会把磁盘上的真 key 覆盖成脱敏串。
        # 识别规则：长度 >= 8 且包含 * 即视为脱敏串，跳过覆盖。
        def _is_masked_key(s: Optional[str]) -> bool:
            return isinstance(s, str) and "*" in s and len(s) >= 8

        if api_key and not _is_masked_key(api_key):  # 真 key 才覆盖
            cfg["api_key"] = api_key
        if base_url is not None and base_url != "":
            cfg["base_url"] = base_url
        elif not cfg.get("base_url"):
            # 旧值已为空（前端编辑失误或首次创建）→ 用 PROVIDER_DEFAULTS 兜底
            cfg["base_url"] = PROVIDER_DEFAULTS[provider_id]["base_url"]
        if model is not None and model != "":
            cfg["model"] = model
        elif not cfg.get("model"):
            cfg["model"] = PROVIDER_DEFAULTS[provider_id]["model"]

        data["providers"][provider_id] = cfg
        self._write(data)
        return dict(cfg)

    def update_default_params(
        self,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        data = self._read()
        if temperature is not None:
            data["default_temperature"] = float(temperature)
        if max_tokens is not None:
            data["default_max_tokens"] = int(max_tokens)
        self._write(data)

    # -------------------- 任务路由 --------------------
    def get_task_routing(self) -> Dict[str, str]:
        """
        返回"任务名 → provider_id" 的路由表。

        取值优先级：stored.task_routing > DEFAULT_TASK_ROUTING。
        _merge_defaults 已保证只包含合法 provider_id。
        """
        return dict(self._read().get("task_routing") or DEFAULT_TASK_ROUTING)

    def get_task_provider(self, task: str) -> str:
        """
        解析 task → provider_id。task 不在路由表时回退到 active_provider。
        路由表只起"按任务偏置"作用，未覆盖的任务仍走通用 active。
        """
        if not task:
            return self.get_active_provider_id()
        return self.get_task_routing().get(task, self.get_active_provider_id())

    def update_task_routing_entry(self, task: str, provider_id: str) -> None:
        """
        覆写单条路由。非法 provider_id 抛 ValueError。
        task 为空串会删除该条（回退到 active_provider）。
        """
        data = self._read()
        if provider_id and provider_id not in (data.get("providers") or {}):
            raise ValueError(f"未知 provider_id: {provider_id}")
        routing = dict(data.get("task_routing") or {})
        if not task:
            raise ValueError("task 不能为空")
        if not provider_id:
            routing.pop(task, None)  # 删除该条
        else:
            routing[task] = provider_id
        data["task_routing"] = routing
        self._write(data)
        logger.info("更新任务路由: %s -> %s", task, provider_id or "(fallback to active)")

    def set_task_routing(self, routing: Dict[str, str]) -> None:
        """
        整体覆写路由表。传 {} 恢复默认（不写盘，靠 _merge_defaults 兜底）。
        """
        data = self._read()
        data["task_routing"] = dict(routing or {})
        self._write(data)
        logger.info("整体覆写任务路由: %d 条", len(routing or {}))

    @staticmethod
    def list_providers_meta() -> List[Dict[str, str]]:
        """返回 provider 元信息（id / name / needs_key），给前端渲染下拉。"""
        return [dict(m) for m in PROVIDER_META]

    def get_provider_with_env_fallback(self, provider_id: str) -> Dict[str, str]:
        """
        取得 provider 配置，缺失字段自动从环境变量补齐。

        设计动机：用户初次启动时 JSON 文件里 api_key 为空（设置页未配置），
        但 .env 中可能有 AGNES_API_KEY 等。此时应让 .env 作为兜底，
        避免出现"配置已存在但系统认为没配置"的诡异状态。

        Returns:
            完整 provider 配置（api_key / base_url / model 一定非空，
            除非连环境变量也没有）。
        """
        import os
        cfg = self.get_provider(provider_id)
        if not cfg.get("api_key"):
            env_val = os.environ.get(f"{provider_id.upper()}_API_KEY")
            if env_val:
                cfg["api_key"] = env_val
        if not cfg.get("base_url"):
            env_val = os.environ.get(f"{provider_id.upper()}_BASE_URL")
            if env_val:
                cfg["base_url"] = env_val
        if not cfg.get("model"):
            env_val = os.environ.get(f"{provider_id.upper()}_MODEL")
            if env_val:
                cfg["model"] = env_val
        return cfg

    # -------------------- 成本控制 --------------------
    def get_budget(self) -> Dict[str, Any]:
        """返回预算控制配置。"""
        return dict(self._read().get("budget") or DEFAULT_BUDGET)

    def update_budget(self, **kwargs) -> Dict[str, Any]:
        """
        更新预算控制配置。支持的字段：
          - enabled: bool
          - daily_limit_yuan: float
          - monthly_limit_yuan: float
          - single_call_token_limit: int
          - alert_threshold_percent: int
        """
        data = self._read()
        budget = dict(data.get("budget") or DEFAULT_BUDGET)
        for key in ["enabled", "daily_limit_yuan", "monthly_limit_yuan",
                    "single_call_token_limit", "alert_threshold_percent"]:
            if key in kwargs and kwargs[key] is not None:
                budget[key] = kwargs[key]
        data["budget"] = budget
        self._write(data)
        logger.info("更新预算控制配置: %s", budget)
        return budget

    # -------------------- 缓存策略 --------------------
    def get_cache(self) -> Dict[str, Any]:
        """返回缓存策略配置。"""
        return dict(self._read().get("cache") or DEFAULT_CACHE)

    def update_cache(self, **kwargs) -> Dict[str, Any]:
        """
        更新缓存策略配置。支持的字段：
          - ttl_seconds: int
          - max_size: int
          - enable_response_cache: bool
          - cache_granularity: str (character / session / global)
        """
        data = self._read()
        cache = dict(data.get("cache") or DEFAULT_CACHE)
        for key in ["ttl_seconds", "max_size", "enable_response_cache", "cache_granularity"]:
            if key in kwargs and kwargs[key] is not None:
                cache[key] = kwargs[key]
        data["cache"] = cache
        self._write(data)
        logger.info("更新缓存策略配置: %s", cache)
        return cache

    # -------------------- 日志监控 --------------------
    def get_logging_config(self) -> Dict[str, Any]:
        """返回日志监控配置。"""
        return dict(self._read().get("logging") or DEFAULT_LOGGING)

    def update_logging_config(self, **kwargs) -> Dict[str, Any]:
        """
        更新日志监控配置。支持的字段：
          - level: str (DEBUG / INFO / WARNING / ERROR)
          - record_api_calls: bool
          - record_token_usage: bool
          - max_log_entries: int
        """
        data = self._read()
        logging_cfg = dict(data.get("logging") or DEFAULT_LOGGING)
        for key in ["level", "record_api_calls", "record_token_usage", "max_log_entries"]:
            if key in kwargs and kwargs[key] is not None:
                logging_cfg[key] = kwargs[key]
        data["logging"] = logging_cfg
        self._write(data)
        logger.info("更新日志监控配置: %s", logging_cfg)
        return logging_cfg

    @staticmethod
    def settings_file_path() -> str:
        """暴露文件路径，方便前端展示与运维排错。"""
        return _SETTINGS_FILE

    @staticmethod
    def mask_api_key(api_key: str) -> str:
        """API Key 脱敏：保留首尾各 4 字符，中间用 **** 代替。"""
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return "****"
        return f"{api_key[:4]}{'*' * max(4, len(api_key) - 8)}{api_key[-4:]}"
