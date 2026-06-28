"""
World CRUD 模块（v1.6 Phase 1 新增）

职责：管理 worlds 表的所有数据库操作。
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session
from backend.models import World, Character

logger = logging.getLogger(__name__)


def create_world(
    db: Session,
    name: str,
    core_worldview: str = "",
) -> World:
    """
    创建一个新世界。

    Args:
        db: 数据库会话
        name: 世界名称
        core_worldview: 核心世界观描述

    Returns:
        新创建的 World 对象
    """
    db_world = World(
        name=name,
        core_worldview=core_worldview,
    )
    db.add(db_world)
    db.commit()
    db.refresh(db_world)
    logger.debug("创建世界: id=%d, name=%s", db_world.id, name)
    return db_world


def get_world(db: Session, world_id: int) -> Optional[World]:
    """根据 ID 获取世界"""
    return db.query(World).filter(World.id == world_id).first()


def get_world_by_character(db: Session, character_id: int) -> Optional[World]:
    """
    通过角色 ID 获取其所属世界。

    实现：先查 Character.world_id，再查 World。
    返回 None 表示角色未关联世界。
    """
    char = db.query(Character).filter(Character.id == character_id).first()
    if not char or not char.world_id:
        return None
    return get_world(db, char.world_id)


def get_all_worlds(db: Session) -> list:
    """获取所有世界列表（按创建时间倒序）。"""
    return db.query(World).order_by(World.created_at.desc()).all()


def update_world(
    db: Session,
    world_id: int,
    name: Optional[str] = None,
    core_worldview: Optional[str] = None,
) -> Optional[World]:
    """
    更新世界信息（部分更新）。

    Args:
        db: 数据库会话
        world_id: 世界 ID
        name: 新名称（None=不修改）
        core_worldview: 新世界观（None=不修改）

    Returns:
        更新后的 World 对象，不存在时返回 None
    """
    world = get_world(db, world_id)
    if not world:
        return None
    if name is not None:
        world.name = name
    if core_worldview is not None:
        world.core_worldview = core_worldview
    db.commit()
    db.refresh(world)
    return world
