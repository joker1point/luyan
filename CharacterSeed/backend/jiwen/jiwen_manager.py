"""
jiwen 引擎管理器 — DB 持久化 + 生命周期

职责：
  1) 把 JiwenEngine 包装为 per-character 单例
  2) 提供 DB 持久化（on_save / on_load callback）
  3) 提供 tick 调度入口（background tick 调用）
  4) 提供触发器消费（contact → push 队列）

设计：
  - 单例：JiwenManager.instance()
  - 每个 character 一份 JiwenEngine（懒加载）
  - 状态变更时落盘（save on apply / tick / set_activity）

测试隔离：
  - 构造函数支持 `session_factory` 参数（callable → Session）
  - 测试可在 setUp 时 monkeypatch 替换为 TestingSessionLocal
  - 不传则用 backend.database.SessionLocal（生产）
"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import Character, ChatSession, Conversation, JiwenState, JiwenTrigger, Memory, ProactiveMessage
from backend.jiwen.jiwen_core import JiwenEngine, JiwenStateSnapshot, create_jiwen

logger = logging.getLogger(__name__)


# ======================================================================
# DB 持久化 helper
# ======================================================================
def _state_row_to_dict(row: JiwenState) -> Dict[str, Any]:
    last_delta = None
    if row.last_delta_json:
        try:
            last_delta = json.loads(row.last_delta_json)
        except Exception:
            last_delta = None
    return {
        "connection": (row.connection or 0) / 100.0,
        "pride":      (row.pride or 0) / 100.0,
        "valence":    (row.valence or 0) / 100.0,
        "arousal":    (row.arousal or 0) / 100.0,
        "immersion":  (row.immersion or 0) / 100.0,
        "last_chat_message_id": row.last_chat_message_id,
        "last_chat_content": row.last_chat_content,
        "last_chat_at": row.last_chat_at.isoformat() if row.last_chat_at else None,
        "user_status": row.user_status or "active",
        "activity_type": row.activity_type or "none",
        "activity_label": row.activity_label,
        "last_tick_at": row.last_tick_at.isoformat() if row.last_tick_at else None,
        "last_delta": last_delta,
        "total_ticks": row.total_ticks or 0,
        "total_contact_triggers": row.total_contact_triggers or 0,
        "total_activity_triggers": row.total_activity_triggers or 0,
        "total_observation_triggers": row.total_observation_triggers or 0,
    }


def _dict_to_state_row(character_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """把 state dict 转为 row 更新字段（不含 updated_at）"""
    def _scale(v: Any, factor: float = 100.0) -> int:
        if v is None:
            return 0
        try:
            return int(round(float(v) * factor))
        except Exception:
            return 0

    def _iso(v: Any) -> Optional[datetime]:
        if not v:
            return None
        try:
            if isinstance(v, datetime):
                return v
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return None

    return {
        "connection": _scale(data.get("connection")),
        "pride":      _scale(data.get("pride")),
        "valence":    _scale(data.get("valence")),
        "arousal":    _scale(data.get("arousal")),
        "immersion":  _scale(data.get("immersion")),
        "last_chat_message_id": data.get("last_chat_message_id"),
        "last_chat_content": data.get("last_chat_content"),
        "last_chat_at": _iso(data.get("last_chat_at")),
        "user_status": data.get("user_status") or "active",
        "activity_type": data.get("activity_type") or "none",
        "activity_label": data.get("activity_label"),
        "last_tick_at": _iso(data.get("last_tick_at")),
        "last_delta_json": json.dumps(data.get("last_delta") or {}, ensure_ascii=False),
        "total_ticks": data.get("total_ticks", 0) or 0,
        "total_contact_triggers": data.get("total_contact_triggers", 0) or 0,
        "total_activity_triggers": data.get("total_activity_triggers", 0) or 0,
        "total_observation_triggers": data.get("total_observation_triggers", 0) or 0,
    }


# ======================================================================
# Manager
# ======================================================================
class JiwenManager:
    """
    jiwen 引擎管理器（per-character 实例缓存 + DB 持久化）

    单例：JiwenManager.instance()
    """

    _instance: Optional["JiwenManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        """
        Args:
            session_factory: 可选的自定义 session 工厂。
                             传 None 时使用 backend.database.SessionLocal（生产）。
                             测试可传 TestingSessionLocal 实现 DB 隔离。
        """
        # character_id → JiwenEngine
        self._engines: Dict[int, JiwenEngine] = {}
        # character_id → 上次 tick 时间戳（用于自动 tick 调度）
        self._last_tick_at: Dict[int, datetime] = {}
        self._lock = threading.RLock()
        self._session_factory = session_factory or SessionLocal

    @classmethod
    def instance(cls) -> "JiwenManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（测试用）"""
        with cls._instance_lock:
            cls._instance = None

    # ----------------------------------------------------------
    # Session helper（带 contextmanager，with self._db() as db:）
    # ----------------------------------------------------------
    @contextmanager
    def _db(self):
        """统一的 session 上下文（生产用 SessionLocal，测试用注入的 factory）

        自动管理事务：正常退出时 commit，异常时 rollback。
        使用 SAVEPOINT（begin_nested）隔离，回滚不会污染外层事务。

        设计要点：
          - 如果外层 session 已在事务中（如测试 fixture 的 autouse 事务），
            begin_nested() 创建 SAVEPOINT，rollback 只回滚到 SAVEPOINT，
            不影响外层事务。
          - 如果外层没有事务（如生产 SessionLocal），begin_nested() 等价于 begin()，
            rollback 回滚整个事务。
        """
        db = self._session_factory()
        try:
            # 使用 SAVEPOINT 隔离
            with db.begin_nested():
                yield db
            # SAVEPOINT 正常 release
        except Exception:
            # SAVEPOINT 异常时自动 rollback
            # 外层 session 状态保持不变
            raise
        finally:
            try:
                db.close()
            except Exception:
                pass

    # ----------------------------------------------------------
    # 引擎获取
    # ----------------------------------------------------------
    def get_engine(
        self,
        character_id: int,
        connection_rate_fn=None,
        rates: Optional[Dict[str, float]] = None,
        thresholds: Optional[Dict[str, float]] = None,
        refresh: bool = False,
    ) -> JiwenEngine:
        """
        获取 per-character jiwen 引擎（懒加载 + 缓存）。

        Args:
            character_id:     角色 ID
            connection_rate_fn: 自定义连接需求增长速率函数（可选）
            rates:            自定义漂移率（可选）
            thresholds:       自定义阈值（可选）
            refresh:          强制重新加载（默认 False）
        """
        with self._lock:
            if character_id in self._engines and not refresh:
                return self._engines[character_id]

            # v008: 若调用方未传 rates/thresholds，从 Character.config.jiwen 读取
            prompt_ctx_template: Optional[str] = None
            style_guide_template: Optional[str] = None
            if rates is None or thresholds is None or True:
                try:
                    with self._db() as db:
                        char = db.query(Character).filter(
                            Character.id == character_id
                        ).first()
                        if char and char.config:
                            cfg = json.loads(char.config)
                            jiwen_cfg = (cfg.get("jiwen", {}) or {}) if isinstance(cfg, dict) else {}
                            if rates is None:
                                r = jiwen_cfg.get("rates")
                                if isinstance(r, dict) and r:
                                    rates = r
                            if thresholds is None:
                                t = jiwen_cfg.get("thresholds")
                                if isinstance(t, dict) and t:
                                    thresholds = t
                            # v008 P2: prompt_templates 自定义（占位符替换）
                            pt = jiwen_cfg.get("prompt_templates") or {}
                            if isinstance(pt, dict):
                                if isinstance(pt.get("context"), str) and pt["context"].strip():
                                    prompt_ctx_template = pt["context"]
                                if isinstance(pt.get("style"), str) and pt["style"].strip():
                                    style_guide_template = pt["style"]
                except Exception as e:
                    logger.warning(
                        "jiwen_manager: 读取 character config 失败 (char_id=%s): %s",
                        character_id, e,
                    )

            engine = create_jiwen(
                character_id=character_id,
                get_last_message=lambda cid=character_id: self._fetch_last_message(cid),
                connection_rate_fn=connection_rate_fn,
                rates=rates,
                thresholds=thresholds,
                on_save=lambda state, cid=character_id: self._save_state_to_db(cid, state),
                on_load=lambda cid=character_id: self._load_state_from_db(cid),
                get_prompt_context_fn=(
                    _build_template_prompt_context_fn(prompt_ctx_template)
                    if prompt_ctx_template
                    else None
                ),
                get_style_guidance_fn=(
                    _build_template_style_guidance_fn(style_guide_template)
                    if style_guide_template
                    else None
                ),
                verbose=False,
            )
            engine.load()
            self._engines[character_id] = engine
            return engine

    def invalidate(self, character_id: int) -> None:
        """清除缓存（角色更新/删除后调用）"""
        with self._lock:
            self._engines.pop(character_id, None)
            self._last_tick_at.pop(character_id, None)

    # ----------------------------------------------------------
    # 高级 API
    # ----------------------------------------------------------
    def tick_character(
        self,
        character_id: int,
        minutes: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        推进单个角色的状态，返回触发器。

        Args:
            character_id: 角色 ID
            minutes: 距上次 tick 的分钟数。None = 自动计算（用 now - last_tick_at）
        """
        engine = self.get_engine(character_id)
        if minutes is None:
            with self._lock:
                last = self._last_tick_at.get(character_id)
            if last is None:
                # 用 jiwen 状态中的 last_tick_at 兜底
                state = engine.get_state()
                last_iso = state.get("last_tick_at")
                last = _parse_iso(last_iso) if last_iso else None
            now = datetime.now(timezone.utc)
            if last is None:
                minutes = 5.0  # 默认 5min
            else:
                minutes = max(0.0, (now - last).total_seconds() / 60.0)

        triggers = engine.tick(minutes)
        engine.save()

        with self._lock:
            self._last_tick_at[character_id] = datetime.now(timezone.utc)

        # 触发器落地
        if triggers:
            trigger_ids = self._persist_triggers(character_id, triggers)
            # 处理 contact 触发器：生成主动消息
            self._handle_contact_triggers(character_id, triggers, trigger_ids)

        return triggers

    def _handle_contact_triggers(
        self,
        character_id: int,
        triggers: List[Dict[str, Any]],
        trigger_ids: List[int],
    ) -> None:
        """
        处理 contact 触发器：异步生成主动消息并入库

        调用 dispatch_proactive_message()，内部通过后台事件循环
        异步执行 LLM 生成（失败时 fallback 到硬编码模板）。

        Args:
            character_id: 角色 ID
            triggers: 触发器列表
            trigger_ids: 对应的触发器 ID 列表
        """
        from backend.modules.proactive import dispatch_proactive_message

        for i, t in enumerate(triggers):
            if t.get("action") == "contact" and i < len(trigger_ids):
                try:
                    state = t.get("state_at_trigger", {})
                    dispatch_proactive_message(
                        character_id=character_id,
                        trigger_state=state,
                        trigger_id=trigger_ids[i],
                        session_factory=self._session_factory,
                    )
                except Exception as e:
                    logger.warning(
                        "jiwen _handle_contact_triggers 失败: character=%d, trigger_id=%d, error=%s",
                        character_id, trigger_ids[i], e,
                    )

    def tick_all_active(self) -> Dict[int, List[Dict[str, Any]]]:
        """
        推进所有"活跃"角色（最近 7 天有过对话的）。

        Returns:
            {character_id: triggers[]}
        """
        result: Dict[int, List[Dict[str, Any]]] = {}
        try:
            with self._db() as db:
                # 简化：所有未删除的 character 都算"活跃"
                characters = db.query(Character).all()
                ids = [c.id for c in characters]
        except Exception as e:
            logger.warning("JiwenManager.tick_all_active 查询角色失败: %s", e)
            return result

        for cid in ids:
            try:
                triggers = self.tick_character(cid)
                if triggers:
                    result[cid] = triggers
            except Exception as e:
                logger.warning("JiwenManager tick char=%d 失败: %s", cid, e)
        return result

    # ----------------------------------------------------------
    # 内部：DB 操作
    # ----------------------------------------------------------
    def _fetch_last_message(self, character_id: int) -> Optional[Dict[str, Any]]:
        """从 conversations 表拉取该角色最后一条消息（jiwen 用作 connectionRateFn 输入）"""
        try:
            with self._db() as db:
                from backend.models import Conversation
                row = (
                    db.query(Conversation)
                    .filter(Conversation.character_id == character_id)
                    .order_by(Conversation.timestamp.desc())
                    .first()
                )
                if not row:
                    return None
                return {
                    "id": row.id,
                    "content": row.user_input or row.npc_response or "",
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                }
        except Exception as e:
            logger.warning("jiwen _fetch_last_message 失败: %s", e)
            return None

    def _save_state_to_db(self, character_id: int, state: Dict[str, Any]) -> None:
        """把 state dict 落库（upsert）"""
        try:
            with self._db() as db:
                row = db.query(JiwenState).filter(JiwenState.character_id == character_id).first()
                fields = _dict_to_state_row(character_id, state)
                if row is None:
                    row = JiwenState(character_id=character_id, **fields)
                    db.add(row)
                else:
                    for k, v in fields.items():
                        setattr(row, k, v)
                # _db() 退出时自动 commit
        except Exception as e:
            logger.warning("jiwen _save_state_to_db 失败: %s", e)

    def _load_state_from_db(self, character_id: int) -> Optional[Dict[str, Any]]:
        """从 DB 加载 state dict"""
        try:
            with self._db() as db:
                row = db.query(JiwenState).filter(JiwenState.character_id == character_id).first()
                if row is None:
                    return None
                return _state_row_to_dict(row)
        except Exception as e:
            logger.warning("jiwen _load_state_from_db 失败: %s", e)
            return None

    def _persist_triggers(self, character_id: int, triggers: List[Dict[str, Any]]) -> List[int]:
        """
        触发器落库

        Returns:
            创建的触发器 ID 列表
        """
        trigger_ids = []
        try:
            with self._db() as db:
                for t in triggers:
                    row = JiwenTrigger(
                        character_id=character_id,
                        action=t.get("action", "observation"),
                        reason=(t.get("reason") or "")[:500],
                        state_json=json.dumps(
                            t.get("state_at_trigger") or {},
                            ensure_ascii=False,
                        )[:5000],
                    )
                    db.add(row)
                    db.flush()  # 获取 ID
                    trigger_ids.append(row.id)
                # _db() 退出时自动 commit
        except Exception as e:
            logger.warning("jiwen _persist_triggers 失败: %s", e)
        return trigger_ids

    # ----------------------------------------------------------
    # 状态查询
    # ----------------------------------------------------------
    def get_state(self, character_id: int) -> Dict[str, Any]:
        """获取角色 jiwen 状态（dict）"""
        engine = self.get_engine(character_id)
        return engine.get_state()

    def get_state_summary(self, character_id: int) -> str:
        engine = self.get_engine(character_id)
        return engine.get_state_summary()

    def get_prompt_context(self, character_id: int) -> str:
        engine = self.get_engine(character_id)
        return engine.get_prompt_context()

    def get_style_guidance(self, character_id: int) -> str:
        engine = self.get_engine(character_id)
        return engine.get_style_guidance()

    def apply_delta(self, character_id: int, delta: Dict[str, float]) -> None:
        """聊天后调整状态"""
        engine = self.get_engine(character_id)
        # [SYNC-1 修复] apply 前 re-read 最新状态，避免对话期间 scheduler tick 覆盖
        engine.load()
        engine.apply_delta(delta)
        engine.save()

    def reset_connection(self, character_id: int) -> None:
        """归零 connection（用户回复主动消息时调用）"""
        engine = self.get_engine(character_id)
        engine.reset_connection()
        engine.save()

    def set_activity(self, character_id: int, activity_type: str, label: Optional[str] = None) -> None:
        engine = self.get_engine(character_id)
        engine.set_activity(activity_type, label)
        engine.save()

    def set_user_status(self, character_id: int, status: str) -> None:
        engine = self.get_engine(character_id)
        engine.set_user_status(status)
        engine.save()

    def set_last_chat_message_id(
        self,
        character_id: int,
        msg_id: int,
        content: Optional[str] = None,
    ) -> None:
        engine = self.get_engine(character_id)
        engine.set_last_chat_message_id(msg_id, content)
        engine.save()

    def get_recent_triggers(
        self,
        character_id: int,
        limit: int = 20,
        action: Optional[str] = None,
        unconsumed_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """获取最近触发器（前端展示用）"""
        try:
            with self._db() as db:
                q = db.query(JiwenTrigger).filter(JiwenTrigger.character_id == character_id)
                if action:
                    q = q.filter(JiwenTrigger.action == action)
                if unconsumed_only:
                    q = q.filter(JiwenTrigger.consumed == 0)
                rows = q.order_by(JiwenTrigger.created_at.desc()).limit(limit).all()
                return [
                    {
                        "id": r.id,
                        "action": r.action,
                        "reason": r.reason,
                        "state": json.loads(r.state_json) if r.state_json else {},
                        "consumed": bool(r.consumed),
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning("jiwen get_recent_triggers 失败: %s", e)
            return []

    def get_proactive_messages(
        self,
        character_id: int,
        limit: int = 10,
        unconsumed_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """获取主动消息（前端轮询用）"""
        try:
            with self._db() as db:
                q = db.query(ProactiveMessage).filter(
                    ProactiveMessage.character_id == character_id
                )
                if unconsumed_only:
                    q = q.filter(ProactiveMessage.consumed == 0)
                rows = q.order_by(ProactiveMessage.created_at.desc()).limit(limit).all()
                return [
                    {
                        "id": r.id,
                        "character_id": r.character_id,
                        "content": r.content,
                        "trigger_id": r.trigger_id,
                        "consumed": bool(r.consumed),
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning("jiwen get_proactive_messages 失败: %s", e)
            return []

    def consume_proactive_message(self, message_id: int) -> bool:
        """标记主动消息为已消费（简单版本，不写入 conversations）"""
        try:
            with self._db() as db:
                msg = db.query(ProactiveMessage).filter(
                    ProactiveMessage.id == message_id
                ).first()
                if msg:
                    msg.consumed = 1
                    # _db() 退出时自动 commit
                    return True
                return False
        except Exception as e:
            logger.warning("jiwen consume_proactive_message 失败: %s", e)
            return False

    def consume_and_insert(self, message_id: int) -> Optional[Dict[str, Any]]:
        """
        消费主动消息并写入 conversations 表（同一事务）。

        流程：
        1. 获取主动消息（含幂等性检查：如果已消费，查已有 conversation）
        2. find_or_create_session（复用最近 24h 的 session 或新建）
        3. 先插入 Conversation（如果失败，consumed 不变 → 可重试）
        4. 再标记 consumed（事务原子性：插入+标记 一起提交）
        5. 返回 session_id 和 conversation_id

        幂等性保护：
        - 如果消息已消费，查询 conversations 表是否有对应记录
        - 有 → 返回已有结果（幂等）
        - 无 → 返回 None（数据异常）

        Args:
            message_id: 主动消息 ID

        Returns:
            {"session_id": int, "conversation_id": int, "character_id": int} 或 None
        """
        try:
            with self._db() as db:
                # 1. 获取主动消息
                msg = db.query(ProactiveMessage).filter(
                    ProactiveMessage.id == message_id,
                ).first()
                if not msg:
                    logger.warning("consume_and_insert: 消息不存在 (id=%s)", message_id)
                    return None

                # 幂等性检查：如果已消费，返回已有 conversation
                if msg.consumed == 1:
                    existing_conv = db.query(Conversation).filter(
                        Conversation.character_id == msg.character_id,
                        Conversation.npc_response == msg.content,
                        Conversation.is_proactive == True,
                    ).order_by(Conversation.id.desc()).first()
                    if existing_conv:
                        logger.info(
                            "consume_and_insert 幂等返回: message_id=%s, conversation_id=%s",
                            message_id, existing_conv.id,
                        )
                        return {
                            "session_id": existing_conv.session_id,
                            "conversation_id": existing_conv.id,
                            "character_id": msg.character_id,
                            "content": msg.content,
                        }
                    logger.warning(
                        "consume_and_insert: 消息已消费但找不到对应 conversation (id=%s)",
                        message_id,
                    )
                    return None

                # 2. find_or_create_session
                session = self._find_or_create_session(db, msg.character_id)
                # 关键：必须在 with 块内缓存需要的 ID，离开后 session/msg 会 detached
                character_id = msg.character_id
                content = msg.content
                session_id = session.id  # int 类型，detached 后仍可读

                # 3. 先插入 Conversation（如果失败，整个事务回滚，consumed 不变）
                conv = Conversation(
                    character_id=character_id,
                    session_id=session_id,
                    user_input="",  # 主动消息无用户输入
                    npc_response=content,
                    is_proactive=True,
                )
                db.add(conv)
                db.flush()  # 提前暴露 SQL 约束错误（而非等到 commit）
                conv_id = conv.id  # int 类型，detached 后仍可读

                # 4. 再标记 consumed（与 Conversation 插入在同一事务中）
                msg.consumed = 1

                # _db() 退出时自动 commit
            # 离开 with 块 → db 已 close，所有 ORM 对象 detached
            # 但 int 字段（id）已缓存，可安全使用

            # 事务已提交
            logger.info(
                "consume_and_insert 成功: message_id=%s, session_id=%s, conversation_id=%s",
                message_id, session_id, conv_id,
            )

            return {
                "session_id": session_id,
                "conversation_id": conv_id,
                "character_id": character_id,
                "content": content,
            }
        except Exception as e:
            logger.error("consume_and_insert 失败: %s", e, exc_info=True)
            return None

    def _find_or_create_session(self, db: Session, character_id: int) -> ChatSession:
        """
        复用最近 N 小时内的 session；超过则新建（默认 24h，可角色级 config 覆盖）

        Args:
            db: 数据库 session
            character_id: 角色 ID

        Returns:
            ChatSession 实例
        """
        from datetime import datetime, timedelta

        # v008: 从 Character.config.session.reuse_window_hours 读取窗口
        reuse_hours = 24
        try:
            char = db.query(Character).filter(Character.id == character_id).first()
            if char and char.config:
                cfg = json.loads(char.config)
                session_cfg = (cfg.get("session", {}) or {}) if isinstance(cfg, dict) else {}
                if "reuse_window_hours" in session_cfg:
                    reuse_hours = session_cfg["reuse_window_hours"]
        except Exception as e:
            logger.debug("读取 reuse_window_hours 失败 (char_id=%s): %s", character_id, e)

        # 查询最近 N 小时内的 session
        cutoff_time = datetime.utcnow() - timedelta(hours=reuse_hours)
        recent_session = db.query(ChatSession).filter(
            ChatSession.character_id == character_id,
            ChatSession.updated_at >= cutoff_time,
        ).order_by(ChatSession.updated_at.desc()).first()

        if recent_session:
            logger.debug("复用最近 session: id=%s, character_id=%s", recent_session.id, character_id)
            return recent_session

        # 新建 session — 标题格式：角色名 · MM-DD HH:MM
        character = db.query(Character).filter(Character.id == character_id).first()
        character_name = character.name if character else "未知角色"
        local_time = datetime.now().strftime("%m-%d %H:%M")
        title = f"{character_name} · {local_time}"

        new_session = ChatSession(
            character_id=character_id,
            title=title,
        )
        db.add(new_session)
        db.flush()  # 获取 ID
        logger.info("新建 session: id=%s, character_id=%s, title=%s",
                    new_session.id, character_id, title)
        return new_session


# ======================================================================
# Utility
# ======================================================================
def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        # 如果解析出的 datetime 没有时区信息，默认使用 UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ======================================================================
# Prompt template helpers (v008 P2)
# ======================================================================
# JiwenStateSnapshot 字段占位符映射（state_attr → snapshot field）
_TEMPLATE_PLACEHOLDER_ATTRS = (
    "connection", "pride", "valence", "arousal", "immersion",
)


def _render_template(template: str, state) -> str:
    """
    用 JiwenStateSnapshot 字段值替换 {field} 占位符。

    示例：
      template = "连接度={connection}, 傲慢={pride}"
      → "连接度=0.50, 傲慢=0.20"
    """
    try:
        mapping = {
            attr: f"{getattr(state, attr, 0.0):.2f}"
            for attr in _TEMPLATE_PLACEHOLDER_ATTRS
        }
        return template.format(**mapping)
    except Exception:
        # 任何占位符错误 → 原样返回（避免 500）
        return template


def _build_template_prompt_context_fn(template: str):
    """
    基于模板字符串构建 get_prompt_context 回调。
    模板使用 {connection} / {pride} / {valence} / {arousal} / {immersion} 占位符。
    """
    def _fn(state) -> str:
        return _render_template(template, state)
    return _fn


def _build_template_style_guidance_fn(template: str):
    """
    基于模板字符串构建 get_style_guidance 回调。占位符同上。
    """
    def _fn(state) -> str:
        return _render_template(template, state)
    return _fn


# ======================================================================
# 便捷函数
# ======================================================================
def get_jiwen_manager() -> JiwenManager:
    return JiwenManager.instance()


__all__ = [
    "JiwenManager",
    "get_jiwen_manager",
]
