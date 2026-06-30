"""
聊天后处理钩子（Post-Chat Hooks）

职责：每次对话完成后（持久化 conversation 后），异步执行：
  1) 更新 jiwen 状态（applyDelta）
  2) 提取记忆（extractor + save）
  3) 巡检衰减（run_decay_pass）
  4) 检查摘要触发（create_summary）

设计：所有调用 wrap 在 try/except，失败不影响主流程。

测试隔离：
  - post_chat_hooks 接受 session_factory 参数（callable → Session）
  - 测试 conftest 在 _isolate_test_state fixture 中 monkeypatch
    `backend.modules.post_chat.SessionLocal` 为 TestingSessionLocal
  - 不传则用 backend.database.SessionLocal（生产）
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import Character, Conversation
from backend.jiwen import get_jiwen_manager
from backend.modules import memory_extractor, memory_decay, summary_trigger
from backend.services.llm_service import LLMService

logger = logging.getLogger(__name__)


# ======================================================================
# 情绪 delta 推断
# ======================================================================
def infer_emotion_delta(
    user_input: str,
    npc_response: str,
    emotion_label: Optional[str] = None,
) -> Dict[str, float]:
    """
    从对话中推断 jiwen 状态 delta（轻量规则版，不调 LLM）。

    Args:
        user_input: 用户输入
        npc_response: 角色回复
        emotion_label: Director 输出的情绪标签

    Returns:
        {pride?, valence?, arousal?, connection?}
    """
    delta: Dict[str, float] = {}

    # 基础规则
    text = (user_input or "").lower()
    pos_words = ["喜欢", "爱", "开心", "谢谢", "好棒", "哈哈", "想你", "感谢", "好"]
    neg_words = ["讨厌", "生气", "滚", "烦", "不想", "不要", "难过", "失望", "哭"]
    intimate_words = ["想你", "想见", "在干嘛", "晚安", "早安", "爱你"]
    critical_words = ["滚", "再见", "分手", "不喜欢你了"]

    if any(w in text for w in critical_words):
        delta["pride"] = 0.3
        delta["valence"] = -0.4
        delta["arousal"] = 0.3
    elif any(w in text for w in neg_words):
        delta["valence"] = -0.2
        delta["arousal"] = 0.15
    elif any(w in text for w in pos_words):
        delta["valence"] = 0.15
        delta["arousal"] = -0.05
    elif any(w in text for w in intimate_words):
        delta["pride"] = -0.1
        delta["valence"] = 0.1
        delta["connection"] = -0.5  # 亲密接触 → 对方回应 → connection 降低

    # 情绪标签加权
    if emotion_label:
        em = (emotion_label or "").strip()
        if em in ("开心", "高兴", "快乐", "兴奋", "愉悦", "满足"):
            delta["valence"] = delta.get("valence", 0) + 0.1
        elif em in ("愤怒", "生气", "恼火"):
            delta["valence"] = delta.get("valence", 0) - 0.1
            delta["arousal"] = delta.get("arousal", 0) + 0.1
        elif em in ("悲伤", "难过", "失落", "沮丧"):
            delta["valence"] = delta.get("valence", 0) - 0.15
            delta["arousal"] = delta.get("arousal", 0) - 0.1
        elif em in ("惊讶", "震惊"):
            delta["arousal"] = delta.get("arousal", 0) + 0.1
        elif em in ("平静", "放松"):
            delta["arousal"] = delta.get("arousal", 0) - 0.05

    return delta


# ======================================================================
# 主入口
# ======================================================================
def post_chat_hooks(
    character_id: int,
    user_input: str,
    npc_response: str,
    emotion_label: Optional[str] = None,
    conversation_id: Optional[int] = None,
    extract_memories: bool = True,
    run_decay: bool = True,
    check_summary: bool = True,
    run_in_background: bool = True,
    session_factory: Optional[Callable[[], Session]] = None,
) -> Dict[str, Any]:
    """
    聊天后处理钩子（一次性调用）。

    Args:
        session_factory: 可选的 session 工厂（callable → Session）。
                         测试可传 TestingSessionLocal 实现 DB 隔离。
                         不传则用 backend.database.SessionLocal（生产）。

    Returns:
        {
            "jiwen_delta": {...},
            "memories_extracted": [ids],
            "decay_result": {"scanned": ..., "forgotten": ...},
            "summary_created": {...} | None,
        }
    """
    if run_in_background:
        thread = threading.Thread(
            target=_run_hooks_sync,
            args=(character_id, user_input, npc_response, emotion_label,
                  conversation_id, extract_memories, run_decay, check_summary,
                  session_factory),
            daemon=True,
        )
        thread.start()
        return {"status": "dispatched", "in_background": True}

    return _run_hooks_sync(
        character_id, user_input, npc_response, emotion_label,
        conversation_id, extract_memories, run_decay, check_summary,
        session_factory,
    )


def _run_hooks_sync(
    character_id: int,
    user_input: str,
    npc_response: str,
    emotion_label: Optional[str],
    conversation_id: Optional[int],
    extract_memories: bool,
    run_decay: bool,
    check_summary: bool,
    session_factory: Optional[Callable[[], Session]] = None,
) -> Dict[str, Any]:
    # 选择 session 工厂（生产 SessionLocal，测试可注入 TestingSessionLocal）
    sf = session_factory or SessionLocal
    result: Dict[str, Any] = {
        "jiwen_delta": {},
        "memories_extracted": [],
        "decay_result": None,
        "summary_created": None,
        "errors": [],
    }

    # 1) jiwen applyDelta
    try:
        # P7: 检测用户是否回复了主动消息（上一条 assistant 消息 is_proactive=True）
        is_reply_to_proactive = False
        if conversation_id:
            try:
                with sf() as db:
                    current_conv = db.query(Conversation).filter(
                        Conversation.id == conversation_id
                    ).first()
                    if current_conv and current_conv.session_id:
                        # 查找同 session 中上一条 assistant 消息
                        prev_msg = db.query(Conversation).filter(
                            Conversation.session_id == current_conv.session_id,
                            Conversation.id < conversation_id,
                            Conversation.is_proactive == True,
                        ).order_by(Conversation.timestamp.desc()).first()
                        if prev_msg:
                            is_reply_to_proactive = True
            except Exception:
                pass

        if is_reply_to_proactive:
            logger.info("post_chat: 用户回复了主动消息，重置 connection")
            get_jiwen_manager().reset_connection(character_id)

        delta = infer_emotion_delta(user_input, npc_response, emotion_label)
        if delta:
            get_jiwen_manager().apply_delta(character_id, delta)
        result["jiwen_delta"] = delta
    except Exception as e:
        logger.warning("post_chat jiwen 失败: %s", e)
        result["errors"].append(f"jiwen: {e}")

    # 1.5) 记录"对方最后说了什么"（jiwen connectionRateFn 用）
    try:
        get_jiwen_manager().set_last_chat_message_id(
            character_id, conversation_id or 0, content=user_input[:500],
        )
    except Exception as e:
        logger.warning("post_chat set_last_chat 失败: %s", e)

    # 2) 提取记忆
    if extract_memories and conversation_id:
        try:
            with sf() as db:
                conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
                if conv:
                    ids = memory_extractor.extract_and_save(
                        db=db, character_id=character_id, conversation=conv,
                    )
                    result["memories_extracted"] = ids
        except Exception as e:
            logger.warning("post_chat extract 失败: %s", e)
            result["errors"].append(f"extract: {e}")

    # 3) 衰减巡检
    if run_decay:
        try:
            with sf() as db:
                result["decay_result"] = memory_decay.run_decay_pass(
                    db=db, character_id=character_id,
                )
        except Exception as e:
            logger.warning("post_chat decay 失败: %s", e)
            result["errors"].append(f"decay: {e}")

    # 4) 摘要触发
    if check_summary:
        try:
            with sf() as db:
                summary = summary_trigger.create_summary(
                    db=db, character_id=character_id, trigger_reason="post_chat_adaptive",
                )
                result["summary_created"] = summary
        except Exception as e:
            logger.warning("post_chat summary 失败: %s", e)
            result["errors"].append(f"summary: {e}")

    return result


__all__ = [
    "infer_emotion_delta",
    "post_chat_hooks",
]
