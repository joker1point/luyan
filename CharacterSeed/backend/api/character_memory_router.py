"""
memory_router — 角色记忆读取端点（注意：原 memory_router.py 是 /api/memory/*，
本文件是 /api/characters/{id}/memories 等读路径，避免与增强记忆系统冲突）。

端点：
  GET /api/characters/{character_id}/memories        记忆列表（按 type 过滤）
  GET /api/characters/{character_id}/conversations   对话历史
  GET /api/characters/{character_id}/growth-logs     成长记录

设计：只读端点，写入在 growth/event 触发时由对应模块负责。
"""
from __future__ import annotations
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import MemoryResponse
from backend.crud import memory as memory_crud
from backend.crud import conversation as conversation_crud
from backend.crud import growth as growth_crud

router = APIRouter(tags=["memory"])


@router.get(
    "/api/characters/{character_id}/memories",
    response_model=List[MemoryResponse],
)
def get_character_memories(
    character_id: int,
    memory_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """获取角色记忆列表。"""
    return memory_crud.get_character_memories(
        db, character_id, memory_type=memory_type, skip=skip, limit=limit,
    )


@router.get("/api/characters/{character_id}/conversations")
def get_character_conversations(
    character_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """获取角色对话历史。"""
    return conversation_crud.get_character_conversations(
        db, character_id, skip=skip, limit=limit,
    )


@router.get("/api/characters/{character_id}/growth-logs")
def get_character_growth_logs(
    character_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """获取角色成长记录列表。"""
    return growth_crud.get_character_growth_logs(
        db, character_id, skip=skip, limit=limit,
    )
