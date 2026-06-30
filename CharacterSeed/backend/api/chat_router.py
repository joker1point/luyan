"""
chat_router — 对话接口（同步 / 流式 SSE）。

端点：
  POST /api/chat        Director + Actor 双 LLM 管线（一次性返回）
  POST /api/chat/stream Director + Actor + Actor.stream（首字即推，TTFT 优化）

SSE 事件格式（每个事件以 \\n\\n 分隔）：
    event: thinking
    data: {"phase":"starting|directing|acting|cache_hit","message":"..."}

    event: meta
    data: {"session_id":1,"session_title":"...","emotion":"...","director_raw":"..."}

    event: speech
    data: {"text":"你好"}

    event: done
    data: {"id":123,"character_id":1,"npc_response":"...","action":"...","expression":"...","actor_raw":"..."}

    event: error
    data: {"message":"..."}
"""
from __future__ import annotations
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Character
from backend.schemas import ChatRequest, ChatResponse
from backend.state import get_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


def _sse(event: str, data: dict) -> str:
    """格式化一个 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    """
    与角色对话（Director + Actor 双 LLM 管线）。

    管线：
      1) 从 DB 拉角色 / 最近记忆
      2) Director.analyze()  → emotion / focus_memories / goal / style
      3) Actor.generate()    → action / expression / speech
      4) 持久化到 conversations
      5) 返回 ChatResponse

    会话管理（NextChat 移植）：
      - request.session_id 缺省/None → 自动创建新 session，标题 = user_message 前 30 字
      - request.session_id 有效       → 复用累积多轮
      - 响应额外返回 session_id / session_title
    """
    try:
        return get_pipeline().run(
            character_id=request.character_id,
            user_message=request.message,
            db=db,
            session_id=request.session_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"对话处理失败: {str(e)}")


@router.post("/api/chat/stream")
def chat_stream(request: ChatRequest, db: Session = Depends(get_db)):
    """
    流式对话接口（SSE — Server-Sent Events）。
    与 /api/chat 的区别：Actor LLM 使用 stream=True，首 token 到达即推送给前端。
    """
    # [PIPE-3 修复] 在 yield 首个事件前预检查角色，避免 HTTP 200 + error event 误导客户端
    character = db.query(Character).filter(Character.id == request.character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail=f"角色不存在: id={request.character_id}")

    def event_generator():
        try:
            for event_type, payload in get_pipeline().run_stream(
                character_id=request.character_id,
                user_message=request.message,
                db=db,
                session_id=request.session_id,
            ):
                if event_type == "thinking":
                    yield _sse("thinking", payload)
                elif event_type == "meta":
                    yield _sse("meta", payload)
                elif event_type == "speech":
                    yield _sse("speech", {"text": payload})
                elif event_type == "error":
                    yield _sse("error", {"message": payload})
                elif event_type == "done":
                    yield _sse("done", payload)
        except Exception as e:
            logger.exception("流式对话异常")
            yield _sse("error", {"message": f"对话处理失败: {str(e)[:200]}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )
