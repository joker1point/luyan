"""
growth_router — 角色成长（Day 3 实现的 Growth LLM 管线）。

端点：
  POST /api/growth/trigger  触发某角色的成长

管线：
  1) 从 DB 拉角色 / 昨日最近对话
  2) GrowthModule.run() → personality_delta / new_memories / event_summary
  3) 计算新人格 = 旧人格 + delta
  4) 持久化 growth_log + memories + 更新 character.personality
  5) 失效该角色的响应缓存（人格/记忆变化后旧缓存必然失效）

注意：Growth 是异步触发接口，不设降级策略——LLM 失败时直接抛异常，
      调用方可自行决定何时重试。
"""
from __future__ import annotations
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import GrowthTriggerRequest, GrowthResponse
from backend.state import get_growth_module
from backend.modules.interaction import cache_invalidate as invalidate_response_cache
from backend.crud import character as character_crud

logger = logging.getLogger(__name__)
router = APIRouter(tags=["growth"])


@router.post("/api/growth/trigger", response_model=GrowthResponse)
def trigger_growth(request: GrowthTriggerRequest, db: Session = Depends(get_db)):
    """触发角色成长。"""
    try:
        result = get_growth_module().run(
            character_id=request.character_id, db=db,
        )
        # 成长后角色人格/记忆变化 → 失效该角色的响应缓存
        invalidated = invalidate_response_cache(character_id=request.character_id)
        logger.info(
            "角色 %d 成长完成，已失效 %d 条响应缓存", request.character_id, invalidated,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"成长处理失败: {str(e)}")
