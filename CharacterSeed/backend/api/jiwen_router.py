"""
jiwen REST API

Endpoints:
  GET    /api/jiwen/characters                         — 列出所有角色
  GET    /api/jiwen/{character_id}/state              — 获取状态
  POST   /api/jiwen/{character_id}/state              — 设置状态（部分）
  POST   /api/jiwen/{character_id}/delta              — 应用 delta
  GET    /api/jiwen/{character_id}/triggers           — 最近触发器
  POST   /api/jiwen/{character_id}/tick               — 立即 tick
  GET    /api/jiwen/{character_id}/prompt-context     — 状态自然语言描述
  GET    /api/jiwen/{character_id}/style-guidance     — 风格指引
  POST   /api/jiwen/{character_id}/activity           — 设置活动
  POST   /api/jiwen/{character_id}/user-status        — 设置对方状态
  POST   /api/jiwen/scheduler/start                   — 启动后台 tick
  POST   /api/jiwen/scheduler/stop                    — 停止后台 tick
  GET    /api/jiwen/scheduler/status                  — 调度器状态
  POST   /api/jiwen/scheduler/tick-now                — 立即 tick all
  GET    /api/jiwen/{character_id}/memory-stats       — 记忆统计（含遗忘比例）
  POST   /api/jiwen/{character_id}/run-decay          — 跑衰减巡检
  POST   /api/jiwen/{character_id}/check-summary      — 强制检查摘要
  GET    /api/jiwen/{character_id}/summaries          — 活跃摘要
  GET    /api/jiwen/{character_id}/proactive-messages — 主动消息队列
  POST   /api/jiwen/{character_id}/proactive-messages/{message_id}/consume — 消费主动消息
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.jiwen import get_jiwen_manager, get_scheduler
from backend.models import Character, Memory, MemorySummary
from backend.modules import memory_decay, summary_trigger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jiwen", tags=["jiwen"])


# ======================================================================
# Schemas
# ======================================================================
class DeltaRequest(BaseModel):
    pride: Optional[float] = None
    valence: Optional[float] = None
    arousal: Optional[float] = None
    connection: Optional[float] = None
    mood: Optional[float] = None  # 别名


class StatePatchRequest(BaseModel):
    user_status: Optional[str] = None
    activity_type: Optional[str] = None
    activity_label: Optional[str] = None


class ActivityRequest(BaseModel):
    activity_type: str
    activity_label: Optional[str] = None


class UserStatusRequest(BaseModel):
    user_status: str


class TickRequest(BaseModel):
    minutes: Optional[float] = None


# ======================================================================
# Helpers
# ======================================================================
def _ensure_character(db: Session, character_id: int) -> None:
    """确保角色存在（不存在则 404）。使用 Depends(get_db) 注入的 session，
    这样测试可通过 app.dependency_overrides[get_db] 注入测试库。"""
    exists = db.query(Character).filter(Character.id == character_id).first()
    if not exists:
        raise HTTPException(status_code=404, detail=f"角色不存在: id={character_id}")


# ======================================================================
# Endpoints
# ======================================================================
@router.get("/characters")
def list_characters(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """列出所有角色（含 jiwen 状态摘要）"""
    mgr = get_jiwen_manager()
    rows = db.query(Character).all()
    result = []
    for c in rows:
        try:
            summary = mgr.get_state_summary(c.id)
        except Exception:
            summary = "(no state)"
        result.append({
            "id": c.id,
            "name": c.name,
            "state_summary": summary,
        })
    return {"characters": result, "count": len(result)}


@router.get("/{character_id}/state")
def get_state(character_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    state = mgr.get_state(character_id)
    summary = mgr.get_state_summary(character_id)
    return {
        "character_id": character_id,
        "state": state,
        "summary": summary,
    }


@router.post("/{character_id}/state")
def patch_state(
    character_id: int, req: StatePatchRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    if req.user_status is not None:
        mgr.set_user_status(character_id, req.user_status)
    if req.activity_type is not None:
        mgr.set_activity(character_id, req.activity_type, req.activity_label)
    return {"status": "ok", "state": mgr.get_state(character_id)}


@router.post("/{character_id}/delta")
def apply_delta(
    character_id: int, req: DeltaRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    delta = req.model_dump(exclude_none=True)
    if not delta:
        raise HTTPException(status_code=400, detail="delta 不能为空")
    mgr = get_jiwen_manager()
    mgr.apply_delta(character_id, delta)
    return {
        "status": "ok",
        "applied_delta": delta,
        "state": mgr.get_state(character_id),
    }


@router.get("/{character_id}/triggers")
def get_triggers(
    character_id: int,
    limit: int = Query(20, ge=1, le=200),
    action: Optional[str] = Query(None),
    unconsumed_only: bool = Query(False),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    triggers = mgr.get_recent_triggers(
        character_id, limit=limit, action=action, unconsumed_only=unconsumed_only,
    )
    return {"character_id": character_id, "count": len(triggers), "triggers": triggers}


@router.post("/{character_id}/tick")
def tick_now(
    character_id: int, req: TickRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    triggers = mgr.tick_character(character_id, minutes=req.minutes)
    return {
        "character_id": character_id,
        "triggers": triggers,
        "state": mgr.get_state(character_id),
    }


@router.get("/{character_id}/prompt-context")
def get_prompt_context(character_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    return {
        "character_id": character_id,
        "context": mgr.get_prompt_context(character_id),
    }


@router.get("/{character_id}/style-guidance")
def get_style_guidance(character_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    return {
        "character_id": character_id,
        "guidance": mgr.get_style_guidance(character_id),
    }


@router.post("/{character_id}/activity")
def set_activity(
    character_id: int, req: ActivityRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    mgr.set_activity(character_id, req.activity_type, req.activity_label)
    return {"status": "ok", "state": mgr.get_state(character_id)}


@router.post("/{character_id}/user-status")
def set_user_status(
    character_id: int, req: UserStatusRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    mgr.set_user_status(character_id, req.user_status)
    return {"status": "ok", "state": mgr.get_state(character_id)}


# ======================================================================
# 调度器
# ======================================================================
@router.post("/scheduler/start")
def scheduler_start(interval_seconds: int = 300) -> Dict[str, Any]:
    sched = get_scheduler()
    # 重设 interval
    sched.interval_seconds = interval_seconds
    sched.start()
    return sched.status()


@router.post("/scheduler/stop")
def scheduler_stop() -> Dict[str, Any]:
    sched = get_scheduler()
    sched.stop()
    return sched.status()


@router.get("/scheduler/status")
def scheduler_status() -> Dict[str, Any]:
    return get_scheduler().status()


@router.post("/scheduler/tick-now")
def scheduler_tick_now() -> Dict[str, Any]:
    sched = get_scheduler()
    result = sched.tick_now()
    triggers_summary = {
        str(cid): [{"action": t["action"], "reason": t.get("reason", "")} for t in ts]
        for cid, ts in result.items()
    }
    return {
        "characters_ticked": len(result),
        "characters_with_triggers": triggers_summary,
    }


# ======================================================================
# 记忆系统
# ======================================================================
@router.get("/{character_id}/memory-stats")
def memory_stats(character_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    total = db.query(Memory).filter(
        Memory.character_id == character_id,
    ).count()
    forgotten = db.query(Memory).filter(
        Memory.character_id == character_id,
        Memory.forgotten == 1,
    ).count()
    ratio = (forgotten / total) if total > 0 else 0.0
    active = total - forgotten

    # 按主题统计
    from sqlalchemy import func
    theme_stats = dict(
        db.query(Memory.theme, func.count(Memory.id))
        .filter(Memory.character_id == character_id)
        .group_by(Memory.theme)
        .all()
    )

    return {
        "character_id": character_id,
        "total": total,
        "active": active,
        "forgotten": forgotten,
        "forgotten_ratio": round(ratio, 4),
        "by_theme": {k or "default": v for k, v in theme_stats.items()},
    }


@router.post("/{character_id}/run-decay")
def run_decay(character_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    result = memory_decay.run_decay_pass(db=db, character_id=character_id)
    return {"character_id": character_id, "result": result}


@router.post("/{character_id}/check-summary")
def check_summary(character_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    decision = summary_trigger.should_summarize(db, character_id)
    if not decision["should"]:
        return {
            "character_id": character_id,
            "triggered": False,
            "decision": decision,
        }
    result = summary_trigger.create_summary(
        db=db, character_id=character_id, trigger_reason="manual",
    )
    return {
        "character_id": character_id,
        "triggered": True,
        "decision": decision,
        "summary": result,
    }


@router.get("/{character_id}/summaries")
def get_summaries(
    character_id: int, limit: int = Query(3, ge=1, le=20),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _ensure_character(db, character_id)
    summaries = summary_trigger.get_active_summaries(db, character_id, limit=limit)
    return {"character_id": character_id, "count": len(summaries), "summaries": summaries}


# ======================================================================
# 主动消息队列
# ======================================================================
@router.get("/{character_id}/proactive-messages")
def get_proactive_messages(
    character_id: int,
    limit: int = Query(10, ge=1, le=100),
    unconsumed_only: bool = Query(True),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """获取主动消息队列（前端轮询用）"""
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    messages = mgr.get_proactive_messages(
        character_id, limit=limit, unconsumed_only=unconsumed_only,
    )
    return {
        "character_id": character_id,
        "count": len(messages),
        "messages": messages,
    }


@router.post("/{character_id}/proactive-messages/{message_id}/consume")
def consume_proactive_message(
    character_id: int,
    message_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """标记主动消息为已消费"""
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    success = mgr.consume_proactive_message(message_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"消息不存在: id={message_id}")
    return {
        "status": "ok",
        "message_id": message_id,
        "consumed": True,
    }
