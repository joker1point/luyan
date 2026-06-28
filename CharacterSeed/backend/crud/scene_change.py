"""
SceneChange CRUD 模块（v1.6 Phase 1 新增）

职责：管理 scene_changes 表的数据库操作，为场景迭代提供叙事记录。
"""
import logging
from typing import Optional, List

from sqlalchemy.orm import Session
from backend.models import SceneChange

logger = logging.getLogger(__name__)


def create_scene_change(
    db: Session,
    scene_id: int,
    change_type: str,
    description: str,
    day_number: int,
    growth_log_id: Optional[int] = None,
    change_details_json: Optional[str] = None,
) -> SceneChange:
    """
    创建一条场景变化记录。

    Args:
        db: 数据库会话
        scene_id: 受影响的场景 ID
        change_type: "character_driven" | "external"
        description: 叙事化的变化描述（核心字段）
        day_number: 发生于第几天
        growth_log_id: 关联的 GrowthLog ID（可选）
        change_details_json: 可选结构化详情 JSON

    Returns:
        新创建的 SceneChange 对象
    """
    db_change = SceneChange(
        scene_id=scene_id,
        growth_log_id=growth_log_id,
        change_type=change_type,
        description=description,
        change_details_json=change_details_json,
        day_number=day_number,
    )
    db.add(db_change)
    db.commit()
    db.refresh(db_change)
    logger.debug(
        "创建场景变更: id=%d, scene=%d, type=%s, day=%d",
        db_change.id, scene_id, change_type, day_number,
    )
    return db_change


def get_recent_changes(
    db: Session,
    scene_id: int,
    limit: int = 20,
) -> List[SceneChange]:
    """
    获取指定场景的最近 N 条变化记录（按 day_number DESC）。

    用于 Growth prompt 注入"最近场景变化"上下文。

    Args:
        db: 数据库会话
        scene_id: 场景 ID
        limit: 最大返回条数，默认 20

    Returns:
        场景变化记录列表（最新在前）
    """
    return (
        db.query(SceneChange)
        .filter(SceneChange.scene_id == scene_id)
        .order_by(SceneChange.day_number.desc())
        .limit(limit)
        .all()
    )


def get_scene_changes_by_world(
    db: Session,
    world_id: int,
    day_number: Optional[int] = None,
    limit: int = 50,
) -> List[SceneChange]:
    """
    获取指定世界中所有场景的变化记录。

    用于前端展示"世界事件时间轴"。

    Args:
        db: 数据库会话
        world_id: 世界 ID
        day_number: 可选筛选天数
        limit: 最大返回条数

    Returns:
        场景变化记录列表（按 day_number DESC）
    """
    from backend.models import Scene
    query = (
        db.query(SceneChange)
        .join(Scene, Scene.id == SceneChange.scene_id)
        .filter(Scene.world_id == world_id)
    )
    if day_number is not None:
        query = query.filter(SceneChange.day_number == day_number)
    return query.order_by(SceneChange.day_number.desc()).limit(limit).all()


def get_scene_changes_by_character(
    db: Session,
    character_id: int,
    limit: int = 50,
) -> List[SceneChange]:
    """
    获取角色所属世界的场景变化记录。

    用于在 Growth 迭代时注入"近期世界发生了什么"上下文。
    """
    from backend.models import Scene, Character
    char = db.query(Character).filter(Character.id == character_id).first()
    if not char or not char.world_id:
        return []
    return get_scene_changes_by_world(db, char.world_id, limit=limit)
