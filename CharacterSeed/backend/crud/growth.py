from sqlalchemy.orm import Session
from backend.models import GrowthLog
from typing import List, Optional

def get_growth_log(db: Session, growth_id: int):
    """获取单条成长记录"""
    return db.query(GrowthLog).filter(GrowthLog.id == growth_id).first()

def get_character_growth_logs(
    db: Session,
    character_id: int,
    skip: int = 0,
    limit: int = 100
):
    """获取角色的所有成长记录（按时间降序）"""
    return db.query(GrowthLog).filter(
        GrowthLog.character_id == character_id
    ).order_by(GrowthLog.created_at.desc()).offset(skip).limit(limit).all()

def create_growth_log(
    db: Session,
    character_id: int,
    personality_delta: Optional[str] = None,
    event_summary: Optional[str] = None,
    new_memories: Optional[str] = None,
    growth_raw: Optional[str] = None,
    # Day4 新增：事件维度输出
    schedule_json: Optional[str] = None,
    world_changes_json: Optional[str] = None,
):
    """创建成长记录（Day4 新增 schedule_json / world_changes_json）"""
    db_growth = GrowthLog(
        character_id=character_id,
        personality_delta=personality_delta,
        event_summary=event_summary,
        new_memories=new_memories,
        growth_raw=growth_raw,
        schedule_json=schedule_json,
        world_changes_json=world_changes_json,
    )
    db.add(db_growth)
    db.commit()
    db.refresh(db_growth)
    return db_growth

def delete_growth_log(db: Session, growth_id: int):
    """删除成长记录"""
    db_growth = db.query(GrowthLog).filter(GrowthLog.id == growth_id).first()
    if db_growth:
        db.delete(db_growth)
        db.commit()
        return True
    return False
