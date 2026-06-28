from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any, Union
from datetime import datetime

# ============================================================
# [角色字段一致性] 中心化字段白名单
# ============================================================
# 设计动机：解决 "models / schemas / crud / API / enrich 脚本" 之间的字段漂移。
#   - CHARACTER_WRITABLE_FIELDS  : API 允许通过 PATCH 写入的字段（创建/更新都用同一份）
#   - CHARACTER_JSON_FIELDS      : 需要做 dict/list → JSON 字符串自动序列化的字段
#   - CHARACTER_IMMUTABLE_FIELDS : 任何路径都不允许修改的字段（id / created_at / 业务规则）
# 任何脚本（enrich / audit / migration）写入字段都必须经过这三组校验，
# 禁止绕过（如 setattr(character, 'any_field', x)）。
# ============================================================

# 可由 API/CRUD 写入的字段子集（不含主键/审计字段）。
# 与 models.Character 保持一一对应；新增字段时必须同时更新这里。
CHARACTER_WRITABLE_FIELDS = frozenset({
    "name",
    "description",
    "world_setting",
    "personality",          # JSON 字符串（dict/str 都会被序列化）
    "current_state",        # JSON 字符串
    "creation_raw",
    "speaking_style",       # JSON 数组字符串
    "values",               # JSON 数组字符串（核心信念）
    "habits",               # JSON 数组字符串
    "long_term_goal",
    "soul_md",              # Markdown
    "day_number",
    "world_id",             # FK → worlds.id
    "current_location_id",  # FK → locations.id
})

# 需要自动 JSON 序列化的字段（CRUD 层统一处理 dict/list → str）
CHARACTER_JSON_FIELDS = frozenset({
    "personality",          # dict
    "current_state",        # dict
    "speaking_style",       # list[str]
    "values",               # list[str]
    "habits",               # list[str]
})

# 任何路径都不允许修改的字段（含主键、时间戳等）
CHARACTER_IMMUTABLE_FIELDS = frozenset({
    "id",
    "created_at",
    "updated_at",
})


# ==================== Character Schemas ====================

class CharacterCreate(BaseModel):
    description: str  # 用户描述（一句话或故事）
    # 注意：文件上传通过FastAPI的UploadFile处理，不在这里定义


# ==================== Description Polish Schemas ====================

class PolishDescriptionRequest(BaseModel):
    """POST /api/characters/polish-description 请求体"""
    description: str = Field(..., min_length=1, max_length=2000, description="待润色的角色描述原文")


class PolishDescriptionResponse(BaseModel):
    """POST /api/characters/polish-description 响应体"""
    polished: str  # 润色后的描述
    original: str  # 原文（便于前端兜底对比）


class SoulUpdateRequest(BaseModel):
    """PUT /api/characters/{character_id}/soul 请求体"""
    soul_md: Optional[str] = Field(None, description="灵魂设定（Markdown 格式）")

class CharacterResponse(BaseModel):
    """
    角色完整响应 schema。

    字段来源严格对齐 backend.models.Character：
      - 必填：id / name / created_at
      - 其余皆为 Optional（与数据库 nullable 保持一致）
      - 画像字段（personality / speaking_style / values / habits / current_state）
        在 DB 中是 JSON 字符串，前端拿到的也是字符串；前端在展示层做 JSON.parse。
      - 新增字段时务必同时更新 CHARACTER_WRITABLE_FIELDS（schemas.py 顶部）。
    """
    # [P0#1 一致性修复] 用 model_config 替代已弃用的 Config；
    #   populate_by_name 让 alias 与原名都能接受（向后兼容）
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    world_setting: Optional[str] = None
    personality: Optional[str] = None  # JSON字符串
    current_state: Optional[str] = None  # JSON字符串
    creation_raw: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # 事件/时间推进所需的画像字段
    day_number: int = 1
    speaking_style: Optional[str] = None  # JSON 数组字符串
    values: Optional[str] = None           # JSON 数组字符串
    habits: Optional[str] = None           # JSON 数组字符串
    long_term_goal: Optional[str] = None
    soul_md: Optional[str] = None          # 灵魂设定（Markdown 格式）
    # v0.4 world pillar：所属世界 / 当前位置
    # [P0#1 一致性修复] 之前缺失，导致前端永远拿不到 world_id / current_location_id
    world_id: Optional[int] = None
    current_location_id: Optional[int] = None


# [P0#1 一致性修复] 新增通用更新请求 schema。
# 字段名与 CharacterResponse 对齐；空值语义 = 不修改（patch 语义）。
# 前端用 PATCH /api/characters/{id}，body 中只放需要改的字段即可。
# 字段写入时由 crud.update_character() 做白名单校验 + JSON 自动序列化。
class CharacterUpdateRequest(BaseModel):
    """PATCH /api/characters/{character_id} 请求体（部分字段可选）"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    world_setting: Optional[str] = None
    # JSON 字段：前端可以传 dict/list（自动序列化）或已序列化的 str。
    # 用 Any 是因为 Pydantic 强类型 dict|str 会让前端传 dict 时报 "str_type" 校验错误。
    personality: Optional[Any] = None
    current_state: Optional[Any] = None
    speaking_style: Optional[Any] = None
    values: Optional[Any] = None
    habits: Optional[Any] = None
    long_term_goal: Optional[str] = None
    soul_md: Optional[str] = None
    day_number: Optional[int] = Field(None, ge=1)
    world_id: Optional[int] = None
    current_location_id: Optional[int] = None

    model_config = ConfigDict(
        # 允许 PATCH 时省略字段（None = 不修改）
        # 拒绝未知字段：防止前端 typo 或后端字段重命名后残留字段污染 DB
        extra="ignore",
    )

# ==================== Conversation Schemas ====================
# ChatRequest 统一在底部 "ChatSession Schemas" 之后定义（带可选 session_id）

class ChatResponse(BaseModel):
    id: int
    character_id: int
    user_input: str
    npc_response: str
    emotion: Optional[str] = None
    action: Optional[str] = None
    expression: Optional[str] = None
    director_raw: Optional[str] = None
    actor_raw: Optional[str] = None
    timestamp: datetime
    session_id: Optional[int] = None  # ← 新增：返回消息所属 session
    session_title: Optional[str] = None  # ← 新增：方便前端立即更新侧栏
    # 全链路耗时埋点（毫秒），用于性能监控与调优
    elapsed_ms: Optional[Dict[str, int]] = None  # {"director": ..., "actor": ..., "persist": ..., "total": ...}

    class Config:
        from_attributes = True

# ==================== Memory Schemas ====================

class MemoryResponse(BaseModel):
    id: int
    character_id: int
    content: str
    importance: int = 5
    memory_type: str = "conversation"
    created_at: datetime
    
    class Config:
        from_attributes = True

# ==================== Growth Schemas ====================

class GrowthTriggerRequest(BaseModel):
    character_id: int

class GrowthResponse(BaseModel):
    id: int
    character_id: int
    personality_delta: Optional[str] = None
    event_summary: Optional[str] = None
    new_memories: Optional[str] = None
    growth_raw: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ==================== Event / Time Schemas ====================
# 配套前端 api/index.ts 中 events.* 三个端点 + list 接口

class EventResponse(BaseModel):
    id: int
    character_id: int
    day_number: int
    order_index: int
    event_type: str
    content: str
    metadata_json: Optional[str] = None
    result_json: Optional[str] = None
    status: str
    session_id: Optional[int] = None
    time_period: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AdvanceRequest(BaseModel):
    """POST /api/event/advance /api/time/iterate /api/time/auto 通用请求体"""
    character_id: int


class IterateResponse(BaseModel):
    """POST /api/time/iterate 的响应（成长+生成次日日程+落库新事件）"""
    growth_log_id: Optional[int] = None
    character_id: int
    day_number: int
    personality_delta: Optional[str] = None
    event_summary: Optional[str] = None
    new_memories: Optional[str] = None
    world_changes_json: Optional[str] = None
    schedule_json: Optional[str] = None
    events_created: int = 0
    growth_raw: Optional[str] = None
    created_at: Optional[datetime] = None
    # [v3.x 新增] 降级标记：growth 阶段 LLM 失败时为 True，前端可显示提示
    growth_degraded: bool = False


class AutoResponse(BaseModel):
    """POST /api/time/auto 的响应（先推进所有 pending，再迭代）"""
    character_id: int
    completed_events: List[EventResponse] = []
    iterate_result: Optional[IterateResponse] = None
    error: Optional[str] = None

# ==================== Creation Response (Special) ====================

class CreationResponse(BaseModel):
    """Creation Module的完整响应"""
    id: int
    name: str
    world_setting: Optional[str] = None
    personality: Optional[str] = None
    initial_memories: Optional[List[str]] = None
    current_state: Optional[str] = None
    creation_raw: Optional[str] = None

# ==================== LLM Settings Schemas ====================

class ProviderMeta(BaseModel):
    """前端下拉选项用的厂商元信息"""
    id: str
    name: str
    needs_key: str  # "true" / "false"（用字符串是因前端 JS 解析方便）


class ProviderConfig(BaseModel):
    """单个 provider 的配置（写入侧：明文 api_key）"""
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class ProviderConfigMasked(BaseModel):
    """单个 provider 的配置（读取侧：api_key 已脱敏）"""
    api_key: str        # 已脱敏：保留首尾 4 字符
    base_url: str
    model: str


class BudgetConfig(BaseModel):
    """预算控制配置"""
    enabled: Optional[bool] = None
    daily_limit_yuan: Optional[float] = None
    monthly_limit_yuan: Optional[float] = None
    single_call_token_limit: Optional[int] = None
    alert_threshold_percent: Optional[int] = None


class CacheConfig(BaseModel):
    """缓存策略配置"""
    ttl_seconds: Optional[int] = None
    max_size: Optional[int] = None
    enable_response_cache: Optional[bool] = None
    cache_granularity: Optional[str] = None  # character / session / global


class LoggingConfig(BaseModel):
    """日志监控配置"""
    level: Optional[str] = None  # DEBUG / INFO / WARNING / ERROR
    record_api_calls: Optional[bool] = None
    record_token_usage: Optional[bool] = None
    max_log_entries: Optional[int] = None


class LLMSettingsResponse(BaseModel):
    """GET /api/settings/llm 的响应体"""
    active_provider: str
    active_provider_name: str
    config: ProviderConfigMasked
    default_temperature: float
    default_max_tokens: int
    providers: dict  # {provider_id: ProviderConfigMasked}
    settings_file_path: str  # 给前端展示用，便于排错
    task_routing: dict = {}  # 任务路由配置
    budget: dict = {}  # 预算控制配置
    cache: dict = {}  # 缓存策略配置
    logging: dict = {}  # 日志监控配置


class LLMUpdateRequest(BaseModel):
    """PUT /api/settings/llm 的请求体（部分字段可选）"""
    active_provider: Optional[str] = None       # 切换激活 provider
    active_config: Optional[ProviderConfig] = None  # 修改当前激活 provider 的配置
    default_temperature: Optional[float] = None
    default_max_tokens: Optional[int] = None
    task_routing: Optional[dict] = None         # 任务路由配置
    budget: Optional[BudgetConfig] = None       # 预算控制配置
    cache: Optional[CacheConfig] = None         # 缓存策略配置
    logging: Optional[LoggingConfig] = None     # 日志监控配置


class LLMTestRequest(BaseModel):
    """POST /api/settings/llm/test 的请求体（可选覆盖当前配置）"""
    # 不传则用当前激活 provider 的配置测试
    provider_id: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    test_prompt: Optional[str] = "你好，请用一句话自我介绍。"


class LLMTestResponse(BaseModel):
    """POST /api/settings/llm/test 的响应体"""
    success: bool
    message: str
    provider_id: str
    model: str
    response_text: Optional[str] = None
    latency_ms: Optional[int] = None


# ==================== API Test Schemas ====================
# 参考 https://github.com/joker1point/web-tools 的 API 联通测试 Dashboard
# 三大能力：models 列表 / 流式延迟 / 原始请求探针

class TestModelItem(BaseModel):
    """provider /v1/models 返回的单个模型条目"""
    id: str
    owned_by: str = ""
    object: str = "model"


class ModelsListResponse(BaseModel):
    """GET /api/test/models 响应体"""
    provider_id: str
    base_url: str
    models: List[TestModelItem]
    duration_ms: int
    raw_count: int


class LatencyTestRequest(BaseModel):
    """POST /api/test/latency 请求体"""
    provider_id: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    test_message: Optional[str] = "Hi"
    max_tokens: Optional[int] = 16


class LatencyTestResponse(BaseModel):
    """POST /api/test/latency 响应体"""
    provider_id: str
    model: str
    status: int
    ttft_ms: Optional[int] = None      # Time To First Token
    total_ms: Optional[int] = None     # 完整响应耗时
    content: str = ""
    chunks: int = 0
    error: Optional[str] = None


class ProbeRequest(BaseModel):
    """POST /api/test/probe 请求体（debug 模式）"""
    provider_id: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    test_message: Optional[str] = "Hi"
    max_tokens: Optional[int] = 16


class ProbeResponse(BaseModel):
    """POST /api/test/probe 响应体（含完整 request/response，密钥脱敏）"""
    provider_id: str
    model: str
    base_url: str
    request: dict
    response: dict
    error: Optional[str] = None


# ==================== ChatSession Schemas ====================
# 参考 https://github.com/ChatGPTNextWeb/NextChat 的会话管理
# 提供：list / create / rename / delete / get-detail（带 messages）/ search

class ChatSessionCreate(BaseModel):
    """POST /api/sessions 请求体"""
    character_id: int
    title: Optional[str] = None  # 缺省时用"新对话"占位


class ChatSessionUpdate(BaseModel):
    """PATCH /api/sessions/{id} 请求体（目前只支持改 title）"""
    title: str


class ChatSessionInfo(BaseModel):
    """会话概要（列表用）"""
    id: int
    character_id: int
    title: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0

    class Config:
        from_attributes = True


class ChatSessionWithMessages(ChatSessionInfo):
    """会话详情（含所有消息）"""
    messages: List["ConversationRow"] = []


class ConversationRow(BaseModel):
    """单条对话（与数据库行 1:1）"""
    id: int
    session_id: Optional[int] = None
    character_id: int
    user_input: str
    npc_response: Optional[str] = None
    emotion: Optional[str] = None
    action: Optional[str] = None
    expression: Optional[str] = None
    director_raw: Optional[str] = None
    actor_raw: Optional[str] = None
    timestamp: Optional[str] = None

    class Config:
        from_attributes = True


# ChatRequest 增加可选的 session_id（向后兼容：None 时自动创建新 session）
class ChatRequest(BaseModel):
    character_id: int
    message: str
    session_id: Optional[int] = None  # ← 新增


# 解决 ChatSessionWithMessages 中 ConversationRow 的前向引用
ChatSessionWithMessages.model_rebuild()


# ==================== Error Log Schemas（日志系统）====================
# 配合 backend/models.py 中的 ErrorLog / AlertConfig 使用。
# 设计要点：
#   - ErrorLogCreate：前端上报错误时使用（level 必填，stack/params 兜底为空）
#   - ErrorLogResponse：列表/详情展示（datetime → iso 字符串）
#   - ErrorLogListResponse：分页响应（含 total + items + 可选 stats）
#   - ErrorLogStats：聚合统计（按 level / type / time_bucket）
#   - ErrorLogTrendBucket：时间序列点（前端画趋势图）
#   - AlertConfigIn/Out：告警配置读写

class ErrorLogCreate(BaseModel):
    """POST /api/logs 请求体：前端上报或后端内部记录"""
    level: str = "ERROR"  # DEBUG / INFO / WARNING / ERROR / CRITICAL
    error_type: str = "backend"  # frontend / backend / database / third_party / internal
    source: Optional[str] = None  # 如 "ChatPage:onSend" / "modules.interaction:run"
    message: str
    stack_trace: Optional[str] = None
    request_path: Optional[str] = None
    request_params: Optional[str] = None  # JSON 字符串
    user_id: Optional[str] = None
    env_info: Optional[str] = None  # JSON 字符串


class ErrorLogResponse(BaseModel):
    """列表 / 详情用：datetime 序列化为 iso 字符串"""
    id: int
    level: str
    error_type: str
    source: Optional[str] = None
    message: str
    stack_trace: Optional[str] = None
    request_path: Optional[str] = None
    request_params: Optional[str] = None
    user_id: Optional[str] = None
    env_info: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True


class ErrorLogListResponse(BaseModel):
    """GET /api/logs 响应体（含分页）"""
    total: int
    items: List[ErrorLogResponse]
    limit: int
    offset: int


class ErrorLogLevelCount(BaseModel):
    level: str
    count: int


class ErrorLogTypeCount(BaseModel):
    error_type: str
    count: int


class ErrorLogTrendBucket(BaseModel):
    """单个时间桶（如 1h）"""
    bucket: str  # 桶起始时间（iso）
    count: int
    by_level: dict = {}  # {"ERROR": 12, "CRITICAL": 1}


class ErrorLogStats(BaseModel):
    """GET /api/logs/stats 响应体：聚合统计"""
    total: int
    by_level: List[ErrorLogLevelCount]
    by_type: List[ErrorLogTypeCount]
    trend: List[ErrorLogTrendBucket]
    range_start: str
    range_end: str


class AlertChannelConfig(BaseModel):
    """单个通知渠道"""
    type: str  # "webhook" / "email" / "console"
    url: Optional[str] = None  # webhook URL
    to: Optional[str] = None  # email 收件人（逗号分隔）
    secret: Optional[str] = None  # webhook 签名密钥（可选）


class AlertConfigIn(BaseModel):
    """PUT /api/logs/alert-config 请求体"""
    enabled: bool = False
    min_level: str = "CRITICAL"  # ERROR / CRITICAL
    channels: List[AlertChannelConfig] = []
    throttle_sec: int = 300


class AlertConfigOut(BaseModel):
    """GET /api/logs/alert-config 响应体"""
    enabled: bool
    min_level: str
    channels: List[AlertChannelConfig] = []
    throttle_sec: int
    updated_at: Optional[str] = None


class LogTestAlertRequest(BaseModel):
    """POST /api/logs/alert-config/test 触发一次测试告警"""
    level: str = "CRITICAL"
    message: str = "Test alert from CharacterSeed"
