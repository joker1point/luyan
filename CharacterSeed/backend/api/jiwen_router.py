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

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.jiwen import get_jiwen_manager, get_scheduler
from backend.models import Character, Memory, MemorySummary
from backend.modules import memory_decay, summary_trigger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jiwen", tags=["jiwen"])


# ======================================================================
# SSE 推送管理（主动消息）
# ======================================================================
# client_id → {"queue": asyncio.Queue, "request": Request, "last_seen": datetime}
_sse_clients: Dict[int, Dict[str, Any]] = {}

# 后台清理任务引用（防止 GC）
_sse_cleanup_task: Optional[asyncio.Task] = None


async def push_proactive_message(message_data: Dict[str, Any]) -> None:
    """
    推送主动消息到所有 SSE 客户端

    带失效客户端检测：5 秒内 put 不进去的客户端会被清理。

    Args:
        message_data: 消息数据，包含 character_id, session_id, content 等
    """
    if not _sse_clients:
        logger.debug("没有 SSE 客户端连接，跳过推送")
        return

    event_data = f"event: proactive_message\ndata: {json.dumps(message_data, ensure_ascii=False)}\n\n"
    stale_clients: list[int] = []

    # 遍历副本，避免修改字典时出错
    for client_id, info in list(_sse_clients.items()):
        queue = info.get("queue") if isinstance(info, dict) else info
        try:
            # 设置超时防止单个慢客户端卡住整个推送
            await asyncio.wait_for(queue.put(event_data), timeout=5.0)
            logger.debug(f"推送主动消息到客户端 {client_id}")
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"推送到客户端 {client_id} 失败: {e}")
            stale_clients.append(client_id)

    # 清理失效客户端
    for cid in stale_clients:
        _sse_clients.pop(cid, None)
        logger.warning(f"清理失效 SSE 客户端: {cid}")


async def _sse_cleanup_loop() -> None:
    """后台任务：每 60 秒清理失效 SSE 客户端（防止客户端崩溃后 entry 残留）"""
    while True:
        try:
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            break
        try:
            stale: list[int] = []
            for cid, info in list(_sse_clients.items()):
                # info 可能是旧格式（裸 queue），兼容处理
                if not isinstance(info, dict):
                    continue
                request = info.get("request")
                try:
                    if request is None or await request.is_disconnected():
                        stale.append(cid)
                except Exception:
                    stale.append(cid)

            for cid in stale:
                _sse_clients.pop(cid, None)
                logger.info(f"SSE 清理任务移除失效客户端: {cid}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"SSE 清理任务异常: {e}")


@router.get("/proactive/stream")
async def proactive_stream(request: Request):
    """
    SSE 长连接，推送主动消息

    客户端连接后会收到所有主动消息的实时推送。
    每 30 秒发送心跳保持连接。
    """
    client_id = id(request)
    queue: asyncio.Queue = asyncio.Queue()
    _sse_clients[client_id] = {
        "queue": queue,
        "request": request,
        "last_seen": datetime.utcnow(),
    }
    logger.info(f"SSE 客户端连接: {client_id}, 当前客户端数: {len(_sse_clients)}")

    # 首次访问时启动后台清理任务
    global _sse_cleanup_task
    if _sse_cleanup_task is None or _sse_cleanup_task.done():
        try:
            loop = asyncio.get_running_loop()
            _sse_cleanup_task = loop.create_task(_sse_cleanup_loop())
            logger.info("SSE 后台清理任务已启动")
        except RuntimeError:
            # 没有 running loop（理论上 FastAPI 内不会发生）
            pass

    async def event_generator():
        try:
            # 发送初始连接成功事件
            yield "event: connected\ndata: {\"status\": \"ok\"}\n\n"

            while True:
                # 检查客户端是否断开
                if await request.is_disconnected():
                    break

                # 更新最后活跃时间
                info = _sse_clients.get(client_id)
                if isinstance(info, dict):
                    info["last_seen"] = datetime.utcnow()

                try:
                    # 等待消息，超时 30 秒发送心跳
                    event_data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event_data
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
        except Exception as e:
            logger.error(f"SSE 流异常: {e}")
        finally:
            _sse_clients.pop(client_id, None)
            logger.info(f"SSE 客户端断开: {client_id}, 剩余客户端数: {len(_sse_clients)}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


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
class SchedulerStartRequest(BaseModel):
    interval_seconds: int = 300
    degraded_interval_seconds: Optional[int] = None
    recovery_interval_seconds: Optional[int] = None
    mode: str = 'normal'  # normal | degraded | recovery


class SchedulerModeRequest(BaseModel):
    mode: str  # normal | degraded | recovery
    degraded_interval_seconds: Optional[int] = None
    recovery_interval_seconds: Optional[int] = None


@router.post("/scheduler/start")
def scheduler_start(req: SchedulerStartRequest) -> Dict[str, Any]:
    """
    启动 jiwen 后台调度器。

    支持配置：
    - interval_seconds: 默认 tick 间隔
    - degraded_interval_seconds: 降级间隔（更高频率节省资源）
    - recovery_interval_seconds: 恢复间隔
    - mode: 启动模式
    """
    sched = get_scheduler()
    sched.interval_seconds = req.interval_seconds
    if req.degraded_interval_seconds is not None:
        sched.degraded_interval_seconds = req.degraded_interval_seconds
    if req.recovery_interval_seconds is not None:
        sched.recovery_interval_seconds = req.recovery_interval_seconds
    if req.mode == 'degraded':
        sched.interval_seconds = sched.degraded_interval_seconds
    sched.mode = req.mode
    sched.start()
    return sched.status()


@router.post("/scheduler/mode")
def scheduler_set_mode(req: SchedulerModeRequest) -> Dict[str, Any]:
    """
    切换调度器模式（normal / degraded / recovery）。
    降级不会停止调度器，只会降低 tick 频率，符合 CRITICAL_MODULE 保护。
    """
    sched = get_scheduler()
    sched.set_mode(
        req.mode,
        degraded_interval=req.degraded_interval_seconds,
        recovery_interval=req.recovery_interval_seconds,
    )
    # 如果调度器没在跑，确保启动
    if not sched._is_running:
        sched.start()
    return sched.status()


@router.post("/scheduler/stop")
def scheduler_stop() -> Dict[str, Any]:
    """
    停止 jiwen 后台调度器。
    警告：jiwen 是 CRITICAL_MODULE，调用此接口会被拒绝。
    请改用 /scheduler/mode 切换到 degraded 模式。
    """
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
async def consume_proactive_message(
    character_id: int,
    message_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    消费主动消息并写入 conversations 表

    流程：
    1. 标记 proactive_messages.consumed = 1
    2. find_or_create_session（复用最近 24h 的 session 或新建）
    3. 插入 conversations 表（is_proactive=True）
    4. 通过 SSE 推送给所有连接的客户端
    5. 返回 session_id 和 conversation_id
    """
    _ensure_character(db, character_id)
    mgr = get_jiwen_manager()
    result = mgr.consume_and_insert(message_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"消息不存在或已消费: id={message_id}")

    # 通过 SSE 推送给所有连接的客户端
    await push_proactive_message({
        "message_id": message_id,
        "character_id": result["character_id"],
        "session_id": result["session_id"],
        "conversation_id": result["conversation_id"],
        "content": result["content"],
        "is_proactive": True,
    })

    return {
        "status": "ok",
        "message_id": message_id,
        "consumed": True,
        "session_id": result["session_id"],
        "conversation_id": result["conversation_id"],
        "character_id": result["character_id"],
    }


# ======================================================================
# 角色可配置参数 (v008)
# ======================================================================
@router.get("/characters/{character_id}/params")
def get_character_params(character_id: int, db: Session = Depends(get_db)):
    """
    获取角色的可配置参数（默认值 + 角色级覆盖的合并结果）。

    返回结构：
      - jiwen: { rates, thresholds, activities, fallback_templates, prompt_templates }
      - decay: { themes, should_forget_threshold }
      - summary: { min_messages_between, max_messages_between,
                   forgotten_ratio_trigger, time_gap_days }
      - session: { reuse_window_hours }
    """
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 默认值
    from backend.jiwen.jiwen_core import DEFAULT_RATES, DEFAULT_THRESHOLDS
    from backend.modules.memory_decay import THEME_DECAY_CONFIG, DEFAULT_SHOULD_FORGET_THRESHOLD
    from backend.modules.summary_trigger import (
        MIN_MESSAGES_BETWEEN, MAX_MESSAGES_BETWEEN,
        FORGOTTEN_RATIO_TRIGGER, TIME_GAP_DAYS,
    )

    # 角色级 config
    config: Dict[str, Any] = {}
    if character.config:
        try:
            config = json.loads(character.config)
        except (json.JSONDecodeError, TypeError):
            config = {}

    jiwen_cfg = config.get("jiwen", {}) if isinstance(config.get("jiwen"), dict) else {}
    decay_cfg = config.get("decay", {}) if isinstance(config.get("decay"), dict) else {}
    summary_cfg = config.get("summary", {}) if isinstance(config.get("summary"), dict) else {}
    session_cfg = config.get("session", {}) if isinstance(config.get("session"), dict) else {}

    rates_override = jiwen_cfg.get("rates") if isinstance(jiwen_cfg.get("rates"), dict) else {}
    thresholds_override = jiwen_cfg.get("thresholds") if isinstance(jiwen_cfg.get("thresholds"), dict) else {}
    activities_override = jiwen_cfg.get("activities") if isinstance(jiwen_cfg.get("activities"), dict) else None
    fallback_override = jiwen_cfg.get("fallback_templates") or []
    prompt_templates_override = jiwen_cfg.get("prompt_templates") or {}
    if not isinstance(prompt_templates_override, dict):
        prompt_templates_override = {}

    themes_override = decay_cfg.get("themes") if isinstance(decay_cfg.get("themes"), dict) else None

    return {
        "character_id": character_id,
        "jiwen": {
            "rates": {**DEFAULT_RATES, **rates_override},
            "thresholds": {**DEFAULT_THRESHOLDS, **thresholds_override},
            "activities": activities_override or {
                "reading": 0.7, "search": 0.5, "browse": 0.4,
                "observe": 0.3, "none": 0.0,
            },
            "fallback_templates": fallback_override,
            "prompt_templates": prompt_templates_override,
        },
        "decay": {
            "themes": themes_override or {
                k: {"base_decay_rate": v[0], "min_half_life_days": v[1],
                    "max_half_life_days": v[2]}
                for k, v in THEME_DECAY_CONFIG.items()
            },
            "should_forget_threshold": decay_cfg.get("should_forget_threshold", DEFAULT_SHOULD_FORGET_THRESHOLD),
        },
        "summary": {
            "min_messages_between": summary_cfg.get("min_messages_between", MIN_MESSAGES_BETWEEN),
            "max_messages_between": summary_cfg.get("max_messages_between", MAX_MESSAGES_BETWEEN),
            "forgotten_ratio_trigger": summary_cfg.get("forgotten_ratio_trigger", FORGOTTEN_RATIO_TRIGGER),
            "time_gap_days": summary_cfg.get("time_gap_days", TIME_GAP_DAYS),
        },
        "session": {
            "reuse_window_hours": session_cfg.get("reuse_window_hours", 24),
        },
    }


@router.put("/characters/{character_id}/params")
def update_character_params(
    character_id: int,
    params: Dict[str, Any],
    db: Session = Depends(get_db),
):
    """
    更新角色的可配置参数（部分更新）。

    请求体即 config JSON 内容（部分更新），会与现有 config 深度 merge。
    会自动刷新对应 jiwen 引擎实例（如果已缓存）。
    """
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    if not isinstance(params, dict):
        raise HTTPException(status_code=422, detail="params 必须为 JSON 对象")

    # 读现有 config
    existing: Dict[str, Any] = {}
    if character.config:
        try:
            existing = json.loads(character.config)
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, TypeError):
            existing = {}

    # 深度 merge（保留未传的字段）
    for key in ("jiwen", "decay", "summary", "session"):
        if key in params and isinstance(params[key], dict):
            existing_key = existing.get(key)
            if isinstance(existing_key, dict):
                existing_key.update(params[key])
            else:
                existing_key = dict(params[key])
            existing[key] = existing_key

    character.config = json.dumps(existing, ensure_ascii=False)
    db.commit()

    # 刷新 jiwen 引擎（如果已缓存）
    # 任何 jiwen 字段（rates/thresholds/prompt_templates 等）变更都需要重建
    # 以确保新模板生效。
    try:
        mgr = get_jiwen_manager()
        jiwen_cfg = existing.get("jiwen", {}) if isinstance(existing.get("jiwen"), dict) else {}
        has_jiwen_update = any(
            jiwen_cfg.get(k) for k in ("rates", "thresholds", "prompt_templates")
        )
        if has_jiwen_update:
            mgr.get_engine(character_id, refresh=True)
    except Exception as e:
        logger.warning("刷新 jiwen 引擎失败（参数已保存）: %s", e)

    return {"status": "ok", "character_id": character_id}
