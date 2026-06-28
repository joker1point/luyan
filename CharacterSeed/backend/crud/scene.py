"""
Scene CRUD 模块（v1.6 Phase 1 新增）

职责：管理 scenes 表的所有数据库操作，包括场景路径查询。
"""
import logging
from typing import Optional, List

from sqlalchemy.orm import Session
from backend.models import Scene, Character

logger = logging.getLogger(__name__)


def create_scene(
    db: Session,
    world_id: int,
    name: str,
    scene_layer: str,
    scene_type: Optional[str] = None,
    parent_scene_id: Optional[int] = None,
    description: Optional[str] = None,
    attributes_json: Optional[str] = None,
    created_day: int = 1,
) -> Scene:
    """
    创建一个新场景。

    scene_layer 约束：必须为 "conceptual" 或 "actual"。
    调用方在传入前应已校验。

    Args:
        db: 数据库会话
        world_id: 所属世界 ID
        name: 场景名称
        scene_layer: "conceptual" | "actual"
        scene_type: 场景类型（continent/kingdom/town/tavern/cave/...）
        parent_scene_id: 父场景 ID（actual 必须指向 conceptual）
        description: 场景描述
        attributes_json: 扩展属性 JSON
        created_day: 创建于第几天

    Returns:
        新创建的 Scene 对象
    """
    db_scene = Scene(
        world_id=world_id,
        name=name,
        scene_layer=scene_layer,
        scene_type=scene_type,
        parent_scene_id=parent_scene_id,
        description=description,
        initial_description=description,  # 初始描述 = 创建时的描述
        attributes_json=attributes_json,
        created_day=created_day,
    )
    db.add(db_scene)
    db.commit()
    db.refresh(db_scene)
    logger.debug("创建场景: id=%d, name=%s, layer=%s", db_scene.id, name, scene_layer)
    return db_scene


def get_scene(db: Session, scene_id: int) -> Optional[Scene]:
    """根据 ID 获取场景"""
    return db.query(Scene).filter(Scene.id == scene_id).first()


def get_scene_path(db: Session, scene_id: int) -> List[Scene]:
    """
    从给定场景向上遍历 parent_scene_id，返回从根到当前的有序路径。

    实现逻辑：
      应用层 while 循环逐层向上查找（最多 ~10 层），
      内部使用 dict 缓存避免重复查询同一场景。

    为什么不用递归 SQL：
      - SQLite 不支持递归 CTE 的部分写法
      - 场景树深度不超过 10，O(10) 次 DB 查询可接受

    Args:
        db: 数据库会话
        scene_id: 起始场景 ID

    Returns:
        从根场景到当前场景的有序列表（根在前，当前在末）
    """
    cache: dict[int, Scene] = {}

    def _get_cached(scene_id_: int) -> Optional[Scene]:
        if scene_id_ not in cache:
            cache[scene_id_] = get_scene(db, scene_id_)
        return cache[scene_id_]

    current = _get_cached(scene_id)
    if not current:
        return []

    path: List[Scene] = []
    while current:
        path.append(current)
        if current.parent_scene_id:
            current = _get_cached(current.parent_scene_id)
        else:
            break

    path.reverse()
    return path


def get_adjacent_scenes(db: Session, scene_id: int) -> List[Scene]:
    """
    获取同一 parent 下的兄弟场景（不含自身）。

    用于向角色展示"附近还有哪些地方可以去"。

    Args:
        db: 数据库会话
        scene_id: 当前场景 ID

    Returns:
        兄弟场景列表（不含自身）
    """
    current = get_scene(db, scene_id)
    if not current or not current.parent_scene_id:
        return []

    return (
        db.query(Scene)
        .filter(
            Scene.parent_scene_id == current.parent_scene_id,
            Scene.id != scene_id,
        )
        .all()
    )


def get_scenes_by_world(
    db: Session,
    world_id: int,
    scene_layer: Optional[str] = None,
) -> List[Scene]:
    """
    获取指定世界下的所有场景。

    Args:
        db: 数据库会话
        world_id: 世界 ID
        scene_layer: 可选筛选层级（"conceptual" / "actual"）

    Returns:
        场景列表（按创建顺序排列）
    """
    query = db.query(Scene).filter(Scene.world_id == world_id)
    if scene_layer:
        query = query.filter(Scene.scene_layer == scene_layer)
    return query.order_by(Scene.id.asc()).all()


def get_scenes_by_character(db: Session, character_id: int) -> List[Scene]:
    """
    获取角色所属世界的所有场景。

    通过 Character.world_id 定位世界，返回全量场景列表。
    """
    char = db.query(Character).filter(Character.id == character_id).first()
    if not char or not char.world_id:
        return []
    return get_scenes_by_world(db, char.world_id)


def get_initial_actual_scene(db: Session, world_id: int) -> Optional[Scene]:
    """
    获取指定世界下第一个实际场景（用于角色创建时的初始位置）。

    遍历 world 下 scene_layer='actual' 的场景，取 ID 最小的。
    """
    return (
        db.query(Scene)
        .filter(Scene.world_id == world_id, Scene.scene_layer == "actual")
        .order_by(Scene.id.asc())
        .first()
    )


def update_scene(
    db: Session,
    scene_id: int,
    description: Optional[str] = None,
    attributes_json: Optional[str] = None,
) -> Optional[Scene]:
    """
    更新场景描述和属性。

    initial_description 不会被修改（保留 Creation 时的原始描述不变）。

    Args:
        db: 数据库会话
        scene_id: 场景 ID
        description: 新描述（None=不修改）
        attributes_json: 新属性 JSON（None=不修改）

    Returns:
        更新后的 Scene 对象，不存在时返回 None
    """
    scene = get_scene(db, scene_id)
    if not scene:
        return None
    if description is not None:
        scene.description = description
    if attributes_json is not None:
        scene.attributes_json = attributes_json
    db.commit()
    db.refresh(scene)
    return scene


def update_current_scene(
    db: Session,
    character_id: int,
    new_scene_id: int,
) -> Optional[Character]:
    """
    更新角色的当前所在场景。

    Args:
        db: 数据库会话
        character_id: 角色 ID
        new_scene_id: 新场景 ID（应为 scene_layer='actual' 的场景）

    Returns:
        更新后的 Character 对象，角色不存在时返回 None
    """
    char = db.query(Character).filter(Character.id == character_id).first()
    if not char:
        logger.warning("update_current_scene: 角色不存在 id=%d", character_id)
        return None

    char.current_scene_id = new_scene_id
    db.commit()
    db.refresh(char)
    logger.debug("角色 %d 移动到场景 %d", character_id, new_scene_id)
    return char
