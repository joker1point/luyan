from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from backend.database import Base

class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)  # 用户原始输入
    world_setting = Column(Text, nullable=True)  # 世界设定（LLM生成）
    personality = Column(Text, nullable=True)  # 人格属性（JSON格式）
    current_state = Column(Text, nullable=True)  # 当前状态（JSON格式）
    creation_raw = Column(Text, nullable=True)  # Creation LLM原始响应
    # 扩展字段：事件/时间推进所需的人物画像
    speaking_style = Column(Text, nullable=True)   # JSON 数组字符串
    values = Column(Text, nullable=True)           # JSON 数组字符串
    habits = Column(Text, nullable=True)           # JSON 数组字符串
    long_term_goal = Column(Text, nullable=True)   # 长期目标
    soul_md = Column(Text, nullable=True)           # 灵魂设定（Markdown 格式，用户可编辑）
    day_number = Column(Integer, nullable=False, default=1)  # 当前天数
    # v0.4 world pillar：所属世界 + 当前位置（外键，渐进替换 current_state.location 字符串）
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="SET NULL"), nullable=True)
    current_location_id = Column(Integer, ForeignKey("locations.id", ondelete="SET NULL"), nullable=True)
    # v008: 角色级配置 JSON（jiwen/decay/summary/session 子键）
    config = Column(Text, nullable=True)
    # v009A: 外貌描述（JSON 字符串，10 字段——height/build/hair_color/.../overall_impression）
    # 用途：给头像/视频生图提供精确的外貌 prompt（避免 LLM 只根据描述自由发挥）
    appearance = Column(Text, nullable=True)
    # v009B: 头像相关字段
    avatar_url = Column(String(500), nullable=True)                 # 当前头像 URL（/avatars/{id}/selected/...）
    avatar_candidates = Column(Text, nullable=True)                 # JSON 数组：候选图 URL 列表
    avatar_selected_index = Column(Integer, default=0)              # 候选图中被选中的下标
    avatar_video_url = Column(String(500), nullable=True)           # 视频头像 URL
    avatar_video_status = Column(String(20), default="none")       # none / pending / generating / completed / failed
    avatar_generation_prompt = Column(Text, nullable=True)          # 生图 prompt（调试用）
    avatar_generated_at = Column(DateTime(timezone=True), nullable=True)  # 最近一次生成时间
    avatar_video_prompt = Column(Text, nullable=True)               # 生视频 prompt
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ChatSession(Base):
    """
    对话会话（多轮消息的容器，参考 NextChat 的 session 概念）

    与 Conversation 的关系：
      - ChatSession 1 → N Conversation
      - 每个 session 有一个 title（自动生成首条消息前缀 or 用户手动改）
      - 删除 session 会级联删除其下所有 conversation
    """
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(
        Integer, ForeignKey("characters.id"), nullable=False, index=True,
    )
    title = Column(String(200), nullable=False, default="新对话")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        index=True,  # 列表页按更新时间倒序，常查
    )


class Conversation(Base):
    __tablename__ = "conversations"
    
    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False, index=True)
    # 会话归属（可空以兼容旧数据；migrate 时会回填到默认 session）
    session_id = Column(
        Integer,
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_input = Column(Text, nullable=False)
    npc_response = Column(Text, nullable=True)
    emotion = Column(String(50), nullable=True)
    action = Column(Text, nullable=True)
    expression = Column(String(100), nullable=True)
    director_raw = Column(Text, nullable=True)  # Director LLM原始响应
    actor_raw = Column(Text, nullable=True)  # Actor LLM原始响应
    is_proactive = Column(Boolean, nullable=False, default=False, index=True)  # 是否为角色主动发起的消息
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class Memory(Base):
    """
    记忆碎片（增强版：jiwen + 遗忘系统）

    字段语义：
      - content:          记忆内容
      - importance:       0-1 重要性评分（extractor 写入）
      - memory_type:      conversation / event / growth / preference / fact / emotion
      - theme:            5 分区（identity / music / taste / moment / todo）—— SonettoHere 5 分区
      - strength:         0-1 当前强度（衰减函数维护，初始=importance）
      - recall_count:     被检索次数（用于 boost 因子）
      - last_recalled_at: 上次被检索时间
      - forgotten:        是否被遗忘（soft-delete，从检索池过滤但保留）
      - decay_rate:       衰减率（按主题差异化：identity 0.001/day，moment 0.05/day）
    """
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    content = Column(Text, nullable=False)
    importance = Column(Integer, default=5)  # 1-10，默认5（保留兼容）
    memory_type = Column(String(50), default="conversation")  # conversation, event, growth, preference, fact, emotion
    # 增强字段（jiwen + 遗忘系统）
    theme = Column(String(20), nullable=True, index=True)  # identity / music / taste / moment / todo
    strength = Column(Integer, default=5)  # 0-10（与 importance 同粒度，方便展示）
    recall_count = Column(Integer, default=0)
    last_recalled_at = Column(DateTime(timezone=True), nullable=True)
    forgotten = Column(Integer, default=0)  # 0/1 bool
    decay_rate = Column(Integer, default=10)  # 0-100，per-day 衰减速率基线
    source_msg_id = Column(Integer, nullable=True)  # 来源 conversation.id
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # 高频：检索时按 character_id + forgotten + strength 排序
        Index("ix_memories_char_active_strength", "character_id", "forgotten", "strength"),
        # 高频：按主题筛选
        Index("ix_memories_char_theme", "character_id", "theme"),
    )


class MemorySummary(Base):
    """
    滚动摘要（L2 记忆）

    设计动机：
      - 50 条滚动摘要固定节奏 → 改为自适应触发（基于 forgotten 比例 + msg_count）
      - 摘要链式：superseded_by 指向"覆盖本条"的新摘要
      - 永不删除旧摘要（保留审计链），但 superseded 后从检索池过滤
    """
    __tablename__ = "memory_summaries"

    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    summary_text = Column(Text, nullable=False)
    msg_start_id = Column(Integer, nullable=True)  # 起始 conversation.id
    msg_end_id = Column(Integer, nullable=True)    # 结束 conversation.id
    msg_count = Column(Integer, default=0)         # 覆盖的对话条数
    importance_score = Column(Integer, default=5)  # 0-10
    # 链式
    superseded_by = Column(Integer, nullable=True)  # 指向新摘要的 id
    is_active = Column(Integer, default=1)          # 0/1，0 = 已被新摘要覆盖
    # 触发原因（自适应触发器记录）
    trigger_reason = Column(String(100), nullable=True)  # msg_count_overflow / forgotten_ratio / manual / initial
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        # 高频：列某角色活跃摘要
        Index("ix_memory_summaries_char_active", "character_id", "is_active"),
    )


class JiwenState(Base):
    """
    jiwen 引擎状态（per character）

    设计动机：
      - jiwen 引擎是 per-character 的，状态需要持久化（重启不丢）
      - 字段直接映射 JiwenStateSnapshot
    """
    __tablename__ = "jiwen_states"

    character_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # 五轴
    connection = Column(Integer, default=0)  # 0-100
    pride = Column(Integer, default=0)       # -100 ~ +100
    valence = Column(Integer, default=0)     # -100 ~ +100
    arousal = Column(Integer, default=0)     # -100 ~ +100
    immersion = Column(Integer, default=0)   # 0-100
    # 元数据
    last_chat_message_id = Column(Integer, nullable=True)
    last_chat_content = Column(Text, nullable=True)
    last_chat_at = Column(DateTime(timezone=True), nullable=True)
    user_status = Column(String(20), default="active")
    activity_type = Column(String(20), default="none")
    activity_label = Column(String(200), nullable=True)
    last_tick_at = Column(DateTime(timezone=True), nullable=True)
    last_delta_json = Column(Text, nullable=True)  # JSON
    # 累计统计
    total_ticks = Column(Integer, default=0)
    total_contact_triggers = Column(Integer, default=0)
    total_activity_triggers = Column(Integer, default=0)
    total_observation_triggers = Column(Integer, default=0)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class JiwenTrigger(Base):
    """
    jiwen 触发记录（用于观察 + 决策审计）

    设计动机：
      - tick() 返回的 triggers 需要落地（前端可查询"角色刚才想找你说话"）
      - 主动开口（contact）单独入 proactive_messages 表或 push 队列
    """
    __tablename__ = "jiwen_triggers"

    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action = Column(String(20), nullable=False)  # contact / find_activity / observation
    reason = Column(String(500), nullable=True)
    state_json = Column(Text, nullable=True)    # 触发时的五轴快照
    consumed = Column(Integer, default=0)        # 0/1，contact 被推送后置 1
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class ProactiveMessage(Base):
    """
    主动消息队列（jiwen contact 触发器消费）

    设计：
      - jiwen tick 产生 contact 触发器时，生成一条主动消息
      - 前端轮询未消费的主动消息并展示
      - 用户查看后置 consumed=1
    """
    __tablename__ = "proactive_messages"

    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = Column(Text, nullable=False)           # 角色主动说的话
    trigger_id = Column(Integer, ForeignKey("jiwen_triggers.id"), nullable=True)
    consumed = Column(Integer, default=0)            # 0/1，用户查看后置 1
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class GrowthLog(Base):
    __tablename__ = "growth_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    personality_delta = Column(Text, nullable=True)  # 人格变化（JSON格式）
    event_summary = Column(Text, nullable=True)
    new_memories = Column(Text, nullable=True)  # 新增记忆（JSON数组）
    growth_raw = Column(Text, nullable=True)  # Growth LLM原始响应
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# ErrorLog（系统日志系统的"严重错误"实时落地表）
# ============================================================
class ErrorLog(Base):
    """
    系统错误日志表（用于"分级存储"中的"严重错误实时写库"）。

    设计：
      - 一条 ErrorLog ≈ 系统发生的一次可观察错误事件
      - 普通 INFO/WARNING 日志走文件系统（usercontext/logs/YYYY-MM-DD.jsonl）
      - ERROR/CRITICAL 日志同步写库（这张表），便于实时查询、告警触发、统计
      - indexed 列覆盖常见查询模式：时间范围 / level / type / user_id

    字段语义：
      - level:        日志等级（DEBUG/INFO/WARNING/ERROR/CRITICAL）
      - error_type:   错误大类（frontend / backend / database / third_party / internal）
      - source:       错误来源定位（"module:function" 或 "Component:method"）
      - message:      错误简述（一句话，便于列表/告警展示）
      - stack_trace:  完整堆栈（多行字符串）
      - request_path: HTTP 请求路径（前端错误可空）
      - request_params: 请求参数（JSON 字符串）
      - user_id:      触发用户标识（前端为 anonymous-xxx，后端为 session-id 等）
      - env_info:     系统环境信息（JSON 字符串：浏览器/操作系统/后端版本等）
      - created_at:   发生时间（带时区）
    """
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String(20), nullable=False, default="ERROR", index=True)
    error_type = Column(String(50), nullable=False, default="backend", index=True)
    source = Column(String(200), nullable=True)
    message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    request_path = Column(String(500), nullable=True, index=True)
    request_params = Column(Text, nullable=True)
    user_id = Column(String(100), nullable=True, index=True)
    env_info = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        # 高频：按 level + 时间倒序 → 告警/统计
        Index("ix_error_logs_level_time", "level", "created_at"),
        # 高频：按 type + 时间倒序 → 趋势分析
        Index("ix_error_logs_type_time", "error_type", "created_at"),
    )


# ============================================================
# AlertConfig（告警通知配置）
# ============================================================
class AlertConfig(Base):
    """
    告警配置表（key=val 风格，单条记录 id=1 即可）。

    设计：
      - enabled:    是否启用告警
      - min_level:  触发告警的最低等级（ERROR / CRITICAL）
      - channels:   通知渠道列表（JSON 数组字符串）
                    每项形如 {"type": "webhook", "url": "...", "secret": "..."}
                    或 {"type": "email", "to": "a@x.com, b@y.com"}
                    或 {"type": "console"}（开发模式）
      - throttle_sec: 同一 message+source 在该秒数内不重复告警
    """
    __tablename__ = "alert_config"

    id = Column(Integer, primary_key=True, index=True)
    enabled = Column(Integer, nullable=False, default=0)
    min_level = Column(String(20), nullable=False, default="CRITICAL")
    channels = Column(Text, nullable=True)  # JSON 数组
    throttle_sec = Column(Integer, nullable=False, default=300)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


# ============================================================
# 复合索引：高频查询模式
# ============================================================
# 1) 会话列表：按 character_id + updated_at desc
Index(
    "ix_chat_sessions_char_updated",
    ChatSession.character_id, ChatSession.updated_at.desc(),
)
# 2) 单会话的消息列表：按 session_id + timestamp
Index(
    "ix_conversations_session_timestamp",
    Conversation.session_id, Conversation.timestamp,
)


# ============================================================
# Event（事件推进）模型
# ============================================================
class Event(Base):
    """
    角色时间线上的事件（用于"事件推进"功能）

    字段语义：
      - day_number:    事件所属的天数（与 characters.day_number 对齐）
      - order_index:   一天内的执行顺序（0-based，越小越早）
      - event_type:    事件类型（schedule_action / scene_event /
                                character_initiative / player_dialogue）
      - content:       事件描述（人类可读的剧情）
      - metadata_json: 附加元数据（对话原文、场景上下文等）
      - result_json:   事件执行结果（Actor 推演后的回执）
      - status:        状态（pending / active / completed）
      - time_period:   时段（morning / afternoon / evening / night）
    """
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_number = Column(Integer, nullable=False, default=1)
    order_index = Column(Integer, nullable=False, default=0)
    event_type = Column(String(50), nullable=False, default="schedule_action")
    content = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    session_id = Column(
        Integer,
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    time_period = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 高频：推进单个事件 = character_id + status + order_index
        Index("ix_events_char_day_order", "character_id", "day_number", "order_index"),
        # 高频：列某角色全部事件 = character_id + day_number
        Index("ix_events_char_status", "character_id", "status"),
    )


# ============================================================================
# v0.4 World Pillar（ADR-009）：4 要素中最薄弱的一环
# 配套设计文档：docs/superpowers/plans/2026-06-27-world-pillar-design.md
# ============================================================================
#
# 设计要点：
#   1) 默认单共享世界（id=1），schema 留 world_id 外键支持多世界扩展
#   2) Location 嵌套树形（parent_id 自引用）+ 树深度 CHECK 限制（≤ 10）
#   3) Item 多态 owner（owner_kind enum + owner_id 多态外键）
#   4) Relationship 无向图（char_a_id < char_b_id 约束 + UNIQUE 去重）
#   5) WorldEvent 世界级事件（与 Event 单角色事件区分）
# ============================================================================


class World(Base):
    """世界（多世界隔离的根）"""
    __tablename__ = "worlds"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    rules_json = Column(Text, nullable=True)  # 物理/魔法规则 JSON
    # 世界级时间状态（与 Character.day_number 区分：世界时间是 day_of_year 1-365）
    season = Column(String(20), nullable=False, default="spring")
    day_of_year = Column(Integer, nullable=False, default=1)
    year = Column(Integer, nullable=False, default=1)
    season_offset = Column(Integer, nullable=False, default=0)  # 南半球 +180
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_worlds_name", "name"),
        CheckConstraint(
            "season IN ('spring','summer','fall','winter')",
            name="ck_worlds_season",
        ),
        CheckConstraint(
            "day_of_year BETWEEN 1 AND 365",
            name="ck_worlds_day_range",
        ),
        CheckConstraint(
            "year >= 1",
            name="ck_worlds_year_positive",
        ),
    )


class Location(Base):
    """地点（嵌套树形）"""
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    world_id = Column(
        Integer,
        ForeignKey("worlds.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id = Column(
        Integer,
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = Column(String(100), nullable=False)
    kind = Column(String(50), nullable=False, default="generic")
    description = Column(Text, nullable=True)
    climate = Column(String(20), nullable=False, default="temperate")
    biome_json = Column(Text, nullable=True)
    capacity = Column(Integer, nullable=True)  # NULL=无限制
    is_public = Column(Boolean, nullable=False, default=True)
    # use_alter=True 打破 characters↔locations 循环外键（Character.current_location_id 也指回 locations）
    owner_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_locations_world", "world_id"),
        Index("ix_locations_parent", "parent_id"),
        Index("ix_locations_name", "name"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id != id",
            name="ck_locations_no_self_ref",
        ),
        CheckConstraint(
            "kind IN ('city','building','room','landscape','dungeon','generic')",
            name="ck_locations_kind",
        ),
    )


class Item(Base):
    """物品/道具（多态 owner）"""
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    world_id = Column(
        Integer,
        ForeignKey("worlds.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    owner_kind = Column(String(20), nullable=False)  # character/location/container
    owner_id = Column(Integer, nullable=False)
    properties_json = Column(Text, nullable=True)
    rarity = Column(String(20), nullable=False, default="common")
    value = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_items_world", "world_id"),
        Index("ix_items_owner", "owner_kind", "owner_id"),
        Index("ix_items_name", "name"),
        CheckConstraint(
            "owner_kind IN ('character','location','container')",
            name="ck_items_owner_kind",
        ),
        CheckConstraint(
            "rarity IN ('common','uncommon','rare','epic','legendary')",
            name="ck_items_rarity",
        ),
    )


class Relationship(Base):
    """NPC 关系网（无向图，char_a_id < char_b_id 约束去重）"""
    __tablename__ = "relationships"

    id = Column(Integer, primary_key=True, index=True)
    world_id = Column(
        Integer,
        ForeignKey("worlds.id", ondelete="CASCADE"),
        nullable=False,
    )
    char_a_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    char_b_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    type = Column(String(30), nullable=False)  # family/friend/lover/rival/mentor/acquaintance
    strength = Column(Integer, nullable=False, default=0)  # -100 ~ +100
    history_json = Column(Text, nullable=True)  # 关系演变事件 JSON
    last_interaction_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_relationships_world", "world_id"),
        Index("ix_relationships_char_a", "char_a_id"),
        Index("ix_relationships_char_b", "char_b_id"),
        # 关键：无向图去重 — 应用层需保证 a < b
        UniqueConstraint("char_a_id", "char_b_id", name="uq_relationships_pair"),
        CheckConstraint(
            "char_a_id < char_b_id",
            name="ck_relationships_order",
        ),
        CheckConstraint(
            "char_a_id != char_b_id",
            name="ck_relationships_no_self",
        ),
        CheckConstraint(
            "type IN ('family','friend','lover','rival','mentor','acquaintance','enemy')",
            name="ck_relationships_type",
        ),
        CheckConstraint(
            "strength BETWEEN -100 AND 100",
            name="ck_relationships_strength_range",
        ),
    )


class WorldEvent(Base):
    """世界级事件（区别于 Event 单角色事件）"""
    __tablename__ = "world_events"

    id = Column(Integer, primary_key=True, index=True)
    world_id = Column(
        Integer,
        ForeignKey("worlds.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id = Column(
        Integer,
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
    )
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    kind = Column(String(30), nullable=False, default="global")  # global/local/seasonal/weather
    scope = Column(String(20), nullable=False, default="public")  # public/private/system
    day = Column(Integer, nullable=False)  # day_of_year
    year = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_world_events_world_day", "world_id", "year", "day"),
        Index("ix_world_events_location", "location_id"),
        CheckConstraint(
            "kind IN ('global','local','seasonal','weather')",
            name="ck_world_events_kind",
        ),
        CheckConstraint(
            "scope IN ('public','private','system')",
            name="ck_world_events_scope",
        ),
        CheckConstraint(
            "day BETWEEN 1 AND 365",
            name="ck_world_events_day_range",
        ),
    )
