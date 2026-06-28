from sqlalchemy.orm import Session
from backend.models import Memory
from typing import List, Optional

def get_memory(db: Session, memory_id: int):
    """获取单条记忆"""
    return db.query(Memory).filter(Memory.id == memory_id).first()

def get_character_memories(
    db: Session,
    character_id: int,
    memory_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
):
    """获取角色的所有记忆（按时间降序，最新的在前）"""
    query = db.query(Memory).filter(Memory.character_id == character_id)
    
    if memory_type:
        query = query.filter(Memory.memory_type == memory_type)
    
    return query.order_by(Memory.created_at.desc()).offset(skip).limit(limit).all()

def create_memory(
    db: Session,
    character_id: int,
    content: str,
    importance: int = 5,
    memory_type: str = "conversation"
):
    """创建记忆"""
    db_memory = Memory(
        character_id=character_id,
        content=content,
        importance=importance,
        memory_type=memory_type
    )
    db.add(db_memory)
    db.commit()
    db.refresh(db_memory)
    return db_memory

def delete_memory(db: Session, memory_id: int):
    """删除记忆"""
    db_memory = db.query(Memory).filter(Memory.id == memory_id).first()
    if db_memory:
        db.delete(db_memory)
        db.commit()
        return True
    return False
