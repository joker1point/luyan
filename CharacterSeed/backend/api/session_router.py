"""
session_router — ChatSession 会话管理（NextChat 移植）。

端点：
  GET    /api/sessions                  列出某角色全部 session（按 title 模糊搜索）
  POST   /api/sessions                  主动创建新会话（预先起标题）
  GET    /api/sessions/{session_id}     会话详情 + 全部消息
  PATCH  /api/sessions/{session_id}     重命名会话
  DELETE /api/sessions/{session_id}     删除会话（级联删除 conversation）

设计要点：
  - 单条 SQL + LEFT JOIN 计算 message_count，避免 N+1
  - ORM 出来的 ChatSession 含 datetime，统一转 iso 字符串
  - 全部依赖 backend.services.chat_session_crud 与 backend.crud.conversation
"""
from __future__ import annotations
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import (
    ChatSessionCreate,
    ChatSessionInfo,
    ChatSessionUpdate,
    ChatSessionWithMessages,
    ConversationRow,
)
from backend.crud import character as character_crud
from backend.crud import conversation as conversation_crud
from backend.services import chat_session_crud

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions", tags=["session"])


def _serialize_session_row(row: dict) -> dict:
    """ORM 出来的 ChatSession 含 datetime，统一转 iso 字符串。"""
    out = dict(row)
    for k in ("created_at", "updated_at"):
        v = out.get(k)
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


@router.get("", response_model=List[ChatSessionInfo])
def list_sessions(
    character_id: int,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """列出某角色的所有 session，支持按 title 模糊搜索。"""
    char = character_crud.get_character(db, character_id)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在: id={character_id}")
    rows = chat_session_crud.list_sessions_with_message_count(
        db, character_id, search=search, limit=limit, offset=offset,
    )
    return [_serialize_session_row(r) for r in rows]


@router.post("", response_model=ChatSessionInfo)
def create_session(request: ChatSessionCreate, db: Session = Depends(get_db)):
    """主动创建新会话（不立刻发消息时使用）。"""
    char = character_crud.get_character(db, request.character_id)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在: id={request.character_id}")
    sess = chat_session_crud.create_session(
        db, request.character_id, title=request.title,
    )
    return _serialize_session_row({
        "id": sess.id,
        "character_id": sess.character_id,
        "title": sess.title,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "message_count": 0,
    })


@router.get("/{session_id}", response_model=ChatSessionWithMessages)
def get_session_detail(session_id: int, db: Session = Depends(get_db)):
    """获取会话详情 + 全部消息（按时间升序）。"""
    sess = chat_session_crud.get_session(db, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail=f"会话不存在: id={session_id}")
    conversations = conversation_crud.get_session_conversations(db, session_id, limit=200)
    messages = []
    for c in conversations:
        row = {
            "id": c.id,
            "session_id": c.session_id,
            "character_id": c.character_id,
            "user_input": c.user_input,
            "npc_response": c.npc_response,
            "emotion": c.emotion,
            "action": c.action,
            "expression": c.expression,
            "director_raw": c.director_raw,
            "actor_raw": c.actor_raw,
            "timestamp": c.timestamp.isoformat() if c.timestamp else None,
        }
        messages.append(ConversationRow.model_validate(row).model_dump(mode="json"))
    info = {
        "id": sess.id,
        "character_id": sess.character_id,
        "title": sess.title,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "message_count": len(messages),
    }
    out = _serialize_session_row(info)
    out["messages"] = messages
    return out


@router.patch("/{session_id}", response_model=ChatSessionInfo)
def update_session(
    session_id: int,
    request: ChatSessionUpdate,
    db: Session = Depends(get_db),
):
    """重命名会话。"""
    sess = chat_session_crud.rename_session(db, session_id, request.title)
    if not sess:
        raise HTTPException(status_code=404, detail=f"会话不存在: id={session_id}")
    msg_count = len(conversation_crud.get_session_conversations(
        db, session_id, limit=1000,
    ))
    return _serialize_session_row({
        "id": sess.id,
        "character_id": sess.character_id,
        "title": sess.title,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "message_count": msg_count,
    })


@router.delete("/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    """删除会话（级联删除其下全部 conversation，依赖外键 ON DELETE CASCADE）。"""
    ok = chat_session_crud.delete_session(db, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"会话不存在: id={session_id}")
    return {"deleted": True, "session_id": session_id}
