"""
自适应摘要触发器（Adaptive Summary Trigger）

替代固定"每 50 条摘要一次"的机械节奏。

触发条件（任一满足即触发）：
  1. 距上次摘要的对话数 >= max(100)  // 上限
  2. 距上次摘要的对话数 >= 20 且 forgotten_ratio > 0.3  // 主题需要"重整"
  3. 距上次摘要的对话数 >= 20 且距上次摘要时间 > 7 天  // 长期未整理
  4. 手动触发（admin / 成长事件）

设计：
  - 上限 100：避免累积太多
  - 下限 20：避免过频
  - forgotten_ratio > 0.3：当前主题已大量"被遗忘"，需要重新摘要整合
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from backend.models import Character, Conversation, MemorySummary
from backend.modules.memory_decay import get_forgotten_ratio
from backend.services.llm_service import LLMService

logger = logging.getLogger(__name__)


# 触发阈值
MIN_MESSAGES_BETWEEN = 20
MAX_MESSAGES_BETWEEN = 100
FORGOTTEN_RATIO_TRIGGER = 0.3
TIME_GAP_DAYS = 7


def should_summarize(
    db: Session,
    character_id: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    判断是否应触发摘要。

    Returns:
        {
            "should": bool,
            "reason": str,
            "msg_count_since_last": int,
            "forgotten_ratio": float,
            "time_since_last_days": float,
        }
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    # 找到最后一条活跃摘要
    last = (
        db.query(MemorySummary)
        .filter(
            MemorySummary.character_id == character_id,
            MemorySummary.is_active == 1,
        )
        .order_by(MemorySummary.created_at.desc())
        .first()
    )

    # 起始锚点
    if last is not None and last.msg_end_id is not None:
        anchor_id = last.msg_end_id
        anchor_time = last.created_at
    else:
        # 没有过摘要 → 从头算
        anchor_id = 0
        anchor_time = None

    # 锚点之后的对话数
    msg_count_q = db.query(Conversation).filter(
        Conversation.character_id == character_id,
    )
    if anchor_id > 0:
        msg_count_q = msg_count_q.filter(Conversation.id > anchor_id)
    msg_count_since_last = msg_count_q.count()

    # 遗忘比例
    forgotten_ratio = get_forgotten_ratio(db, character_id)

    # 时间间隔
    if anchor_time:
        if anchor_time.tzinfo is None:
            anchor_time = anchor_time.replace(tzinfo=timezone.utc)
        time_since = max(0.0, (now.replace(tzinfo=timezone.utc) - anchor_time).total_seconds() / 86400.0)
    else:
        time_since = 999.0

    should = False
    reason = ""

    if msg_count_since_last >= MAX_MESSAGES_BETWEEN:
        should = True
        reason = f"msg_count_overflow ({msg_count_since_last} >= {MAX_MESSAGES_BETWEEN})"
    elif msg_count_since_last < MIN_MESSAGES_BETWEEN:
        should = False
        reason = f"msg_count_too_few ({msg_count_since_last} < {MIN_MESSAGES_BETWEEN})"
    elif forgotten_ratio > FORGOTTEN_RATIO_TRIGGER:
        should = True
        reason = f"forgotten_ratio ({forgotten_ratio:.2f} > {FORGOTTEN_RATIO_TRIGGER})"
    elif time_since > TIME_GAP_DAYS:
        should = True
        reason = f"time_gap ({time_since:.1f} days > {TIME_GAP_DAYS})"
    else:
        should = False
        reason = "no trigger"

    return {
        "should": should,
        "reason": reason,
        "msg_count_since_last": msg_count_since_last,
        "forgotten_ratio": forgotten_ratio,
        "time_since_last_days": time_since,
    }


def build_summary(
    db: Session,
    character_id: int,
    msg_start_id: int,
    msg_end_id: int,
    llm_service: Optional[LLMService] = None,
) -> str:
    """
    调用 LLM 把区间内对话摘要。返回摘要文本。
    """
    conversations = (
        db.query(Conversation)
        .filter(
            Conversation.character_id == character_id,
            Conversation.id >= msg_start_id,
            Conversation.id <= msg_end_id,
        )
        .order_by(Conversation.timestamp.asc())
        .limit(80)
        .all()
    )
    if not conversations:
        return ""

    lines = []
    for c in conversations:
        u = (c.user_input or "").strip()
        n = (c.npc_response or "").strip()
        if u and n:
            lines.append(f"[U]: {u}\n[N]: {n}")
    if not lines:
        return ""
    text = "\n\n".join(lines)

    prompt = (
        "把以下对话压缩为一段简洁的'角色记忆摘要'（150-300 字）。\n"
        "保留关键事件、用户透露的偏好/事实、角色的反应。\n"
        "用第三人称。不要任何寒暄。\n\n"
        f"对话：\n{text}\n\n摘要："
    )

    try:
        llm = llm_service or LLMService()
        return llm.call(
            prompt=prompt,
            system_prompt="你是一个对话摘要助手。",
            temperature=0.3,
            task="summary",
        ).strip()
    except Exception as e:
        logger.warning("build_summary LLM 失败: %s", e)
        return ""


def create_summary(
    db: Session,
    character_id: int,
    trigger_reason: str = "adaptive",
    llm_service: Optional[LLMService] = None,
) -> Optional[Dict[str, Any]]:
    """
    主入口：检查 + 创建新摘要（标 supersede 旧摘要）。

    Returns:
        {"id": int, "summary_text": str, "msg_count": int, "trigger_reason": str}
        若未触发返回 None
    """
    decision = should_summarize(db, character_id)
    if not decision["should"]:
        return None

    # 找到起始锚点（最后一条 active 摘要的 msg_end_id）
    last = (
        db.query(MemorySummary)
        .filter(
            MemorySummary.character_id == character_id,
            MemorySummary.is_active == 1,
        )
        .order_by(MemorySummary.created_at.desc())
        .first()
    )
    msg_start_id = (last.msg_end_id + 1) if (last and last.msg_end_id) else 0

    # 找最新一条 conversation
    latest_conv = (
        db.query(Conversation)
        .filter(Conversation.character_id == character_id)
        .order_by(Conversation.id.desc())
        .first()
    )
    if not latest_conv:
        return None
    msg_end_id = latest_conv.id

    if msg_start_id > msg_end_id:
        return None

    summary_text = build_summary(
        db=db,
        character_id=character_id,
        msg_start_id=msg_start_id,
        msg_end_id=msg_end_id,
        llm_service=llm_service,
    )
    if not summary_text:
        return None

    try:
        # 创建新摘要
        msg_count = (msg_end_id - msg_start_id + 1)
        new_summary = MemorySummary(
            character_id=character_id,
            summary_text=summary_text,
            msg_start_id=msg_start_id,
            msg_end_id=msg_end_id,
            msg_count=msg_count,
            importance_score=6,  # 摘要默认中上重要性
            is_active=1,
            trigger_reason=trigger_reason or decision["reason"],
        )
        db.add(new_summary)
        db.flush()

        # 链式：把旧 active 摘要标为 superseded
        if last is not None:
            last.is_active = 0
            last.superseded_by = new_summary.id

        db.commit()
        return {
            "id": new_summary.id,
            "summary_text": summary_text,
            "msg_count": msg_count,
            "trigger_reason": new_summary.trigger_reason,
            "msg_start_id": msg_start_id,
            "msg_end_id": msg_end_id,
        }
    except Exception as e:
        logger.error("create_summary 失败: %s", e)
        db.rollback()
        return None


def get_active_summaries(
    db: Session,
    character_id: int,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """获取某角色的活跃摘要（按时间倒序）"""
    rows = (
        db.query(MemorySummary)
        .filter(
            MemorySummary.character_id == character_id,
            MemorySummary.is_active == 1,
        )
        .order_by(MemorySummary.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "summary_text": r.summary_text,
            "msg_count": r.msg_count,
            "msg_start_id": r.msg_start_id,
            "msg_end_id": r.msg_end_id,
            "importance_score": r.importance_score,
            "trigger_reason": r.trigger_reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


__all__ = [
    "should_summarize",
    "build_summary",
    "create_summary",
    "get_active_summaries",
    "MIN_MESSAGES_BETWEEN",
    "MAX_MESSAGES_BETWEEN",
    "FORGOTTEN_RATIO_TRIGGER",
    "TIME_GAP_DAYS",
]
