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
from backend.models import Character, JiwenState, JiwenTrigger, Memory, ProactiveMessage
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
        """统一的 session 上下文（生产用 SessionLocal，测试用注入的 factory）"""
        db = self._session_factory()
        try:
            yield db
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

            engine = create_jiwen(
                character_id=character_id,
                get_last_message=lambda cid=character_id: self._fetch_last_message(cid),
                connection_rate_fn=connection_rate_fn,
                rates=rates,
                thresholds=thresholds,
                on_save=lambda state, cid=character_id: self._save_state_to_db(cid, state),
                on_load=lambda cid=character_id: self._load_state_from_db(cid),
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
        处理 contact 触发器：生成主动消息并入库

        Args:
            character_id: 角色 ID
            triggers: 触发器列表
            trigger_ids: 对应的触发器 ID 列表
        """
        try:
            with self._db() as db:
                for i, t in enumerate(triggers):
                    if t.get("action") == "contact" and i < len(trigger_ids):
                        # 生成主动消息内容（基于状态）
                        state = t.get("state_at_trigger", {})
                        connection = state.get("connection", 0)
                        pride = state.get("pride", 0)

                        # 根据情绪状态生成不同的开场白
                        if connection >= 0.5:
                            if pride >= 0.3:
                                content = "（嘴硬地）人呢？怎么不说话了？"
                            else:
                                content = "在忙吗？想找你聊聊。"
                        elif connection >= 0.35:
                            if pride >= 0.3:
                                content = "（犹豫了一下）...在吗？"
                            else:
                                content = "最近怎么样？"
                        else:
                            content = "嘿，有空吗？"

                        # 入库
                        msg = ProactiveMessage(
                            character_id=character_id,
                            content=content,
                            trigger_id=trigger_ids[i],
                        )
                        db.add(msg)
                db.commit()
        except Exception as e:
            logger.warning("jiwen _handle_contact_triggers 失败: %s", e)

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
                db.commit()
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
                db.commit()
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
        engine.apply_delta(delta)
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
        """标记主动消息为已消费"""
        try:
            with self._db() as db:
                msg = db.query(ProactiveMessage).filter(
                    ProactiveMessage.id == message_id
                ).first()
                if msg:
                    msg.consumed = 1
                    db.commit()
                    return True
                return False
        except Exception as e:
            logger.warning("jiwen consume_proactive_message 失败: %s", e)
            return False


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
# 便捷函数
# ======================================================================
def get_jiwen_manager() -> JiwenManager:
    return JiwenManager.instance()


__all__ = [
    "JiwenManager",
    "get_jiwen_manager",
]
