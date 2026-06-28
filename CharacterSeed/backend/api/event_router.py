"""
event_router — 事件 / 时间推进（Day 4 事件推进）。

端点：
  GET  /api/characters/{character_id}/events  列出某角色全部事件（含 day_number / status 过滤）
  POST /api/event/advance                    推进一个 pending 事件（LLM 推演）
  POST /api/time/iterate                     日迭代（成长 + 生成次日 schedule + 落库）
  POST /api/time/auto                        一键推演（先推完 pending → 再 iterate）

设计：
  - _time_engine.auto 内部已捕获异常并返回结构化 error，因此端点本身不抛 5xx
  - advance_event 若无 pending 事件，返回 404 + 友好文案
"""
from __future__ import annotations
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import (
    EventResponse,
    AdvanceRequest,
    IterateResponse,
    AutoResponse,
)
from backend.crud import character as character_crud
from backend.crud import event as event_crud
from backend.state import get_event_manager, get_time_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["event"])


@router.get(
    "/api/characters/{character_id}/events",
    response_model=List[EventResponse],
)
def list_character_events(
    character_id: int,
    day_number: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """列出某角色的全部事件（按 day_number, order_index 升序）。"""
    if not character_crud.get_character(db, character_id):
        raise HTTPException(status_code=404, detail=f"角色不存在: id={character_id}")
    return event_crud.list_events(
        db, character_id,
        day_number=day_number, status=status,
    )


@router.post("/api/event/advance", response_model=EventResponse)
def advance_event(request: AdvanceRequest, db: Session = Depends(get_db)):
    """
    推进一个 pending 事件：
      1) 取该角色下一个 status=pending 的事件（按 day, order 升序）
      2) 调 LLM 生成 result_text + narrative_delta
      3) 写入 result_json, status=completed
      4) 若无 pending 事件，返回 404 + 友好提示
    """
    if not character_crud.get_character(db, request.character_id):
        raise HTTPException(
            status_code=404, detail=f"角色不存在: id={request.character_id}",
        )
    try:
        updated = get_event_manager().advance_one(db, request.character_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"事件推进失败: {str(e)}")

    if updated is None:
        pending = event_crud.count_pending_events(db, request.character_id)
        raise HTTPException(
            status_code=404,
            detail=f"角色 {request.character_id} 当前无 pending 事件（pending={pending}）",
        )
    return updated


@router.post("/api/time/iterate", response_model=IterateResponse)
def iterate_time(request: AdvanceRequest, db: Session = Depends(get_db)):
    """
    迭代到下一天（成长 + 生成次日 schedule + 落库新事件 + day_number+1）。
    """
    if not character_crud.get_character(db, request.character_id):
        raise HTTPException(
            status_code=404, detail=f"角色不存在: id={request.character_id}",
        )
    try:
        result = get_time_engine().iterate(db, request.character_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"时间迭代失败: {str(e)}")
    return IterateResponse(**result)


@router.post("/api/time/auto", response_model=AutoResponse)
def auto_advance(request: AdvanceRequest, db: Session = Depends(get_db)):
    """
    一键推演：先推进所有 pending 事件，再迭代到下一天。
    任一阶段异常都返回结构化 error，不抛 500。
    """
    if not character_crud.get_character(db, request.character_id):
        raise HTTPException(
            status_code=404, detail=f"角色不存在: id={request.character_id}",
        )
    try:
        result = get_time_engine().auto(db, request.character_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"一键推演失败: {str(e)}")

    iter_dict = result.get("iterate_result")
    if iter_dict is not None:
        iter_dict = IterateResponse(**iter_dict)
    return AutoResponse(
        character_id=result["character_id"],
        completed_events=[
            EventResponse.model_validate(e) for e in result.get("completed_events", [])
        ],
        iterate_result=iter_dict,
        error=result.get("error"),
    )
