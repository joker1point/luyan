from sqlalchemy.orm import Session
from backend.models import Conversation
from typing import List, Optional

def get_conversation(db: Session, conversation_id: int):
    """获取单条对话"""
    return db.query(Conversation).filter(Conversation.id == conversation_id).first()

def get_character_conversations(
    db: Session,
    character_id: int,
    skip: int = 0,
    limit: int = 100
):
    """获取角色的所有对话（按时间升序）"""
    return db.query(Conversation).filter(
        Conversation.character_id == character_id
    ).order_by(Conversation.timestamp.asc()).offset(skip).limit(limit).all()

def create_conversation(
    db: Session,
    character_id: int,
    user_input: str,
    npc_response: str,
    emotion: Optional[str] = None,
    action: Optional[str] = None,
    expression: Optional[str] = None,
    director_raw: Optional[str] = None,
    actor_raw: Optional[str] = None,
    session_id: Optional[int] = None,
):
    """创建对话记录"""
    db_conversation = Conversation(
        character_id=character_id,
        user_input=user_input,
        npc_response=npc_response,
        emotion=emotion,
        action=action,
        expression=expression,
        director_raw=director_raw,
        actor_raw=actor_raw,
        session_id=session_id,
    )
    db.add(db_conversation)
    db.commit()
    db.refresh(db_conversation)
    return db_conversation


def get_session_conversations(
    db: Session,
    session_id: int,
    limit: int = 200,
) -> List[Conversation]:
    """获取某个 session 的全部对话（按时间升序）"""
    return (
        db.query(Conversation)
        .filter(Conversation.session_id == session_id)
        .order_by(Conversation.timestamp.asc())
        .limit(limit)
        .all()
    )

def delete_conversation(db: Session, conversation_id: int):
    """删除对话"""
    db_conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if db_conversation:
        db.delete(db_conversation)
        db.commit()
        return True
    return False
