"""
Location 树形查询工具（ADR-009 / 2026-06-27-world-pillar-design.md §2.2）

设计要点：
  1) 嵌套树形（parent_id 自引用）
  2) 树深度防御：应用层限制 ≤ 10（DB CHECK 禁止自引用）
  3) 纯 SQL 查询 + Python 拼装，避免 ORM 循环触发 lazy load
  4) 防御性：循环引用（虽然 DB 阻止） + 不存在的节点

关键 API：
  - path_to_root(location) -> [root, ..., leaf]
  - children_of(parent_id) -> [Location, ...]
  - siblings(location) -> [Location, ...] (不含自己)
  - root_of(location) -> Location
  - depth_of(location) -> int
"""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from backend.models import Location

MAX_TREE_DEPTH = 10  # 应用层防御


def _get_location(db: Session, location_id: int) -> Optional[Location]:
    return db.query(Location).filter(Location.id == location_id).first()


def path_to_root(db: Session, location_id: int) -> List[Location]:
    """
    从叶子到根的路径（含起止节点）。

    例：东京(1) → 涩谷(2) → 猫头鹰咖啡馆(3) 的 path_to_root(3)
       → [猫头鹰咖啡馆, 涩谷, 东京]

    防御：
      - depth > MAX_TREE_DEPTH 直接抛 RuntimeError（数据腐败）
      - 循环引用检测：visited set
    """
    loc = _get_location(db, location_id)
    if not loc:
        return []

    path: List[Location] = [loc]
    visited = {loc.id}
    current = loc
    depth = 0
    while current.parent_id is not None:
        depth += 1
        if depth > MAX_TREE_DEPTH:
            raise RuntimeError(
                f"Location tree too deep (> {MAX_TREE_DEPTH}), "
                f"id={location_id} may have cycle"
            )
        if current.parent_id in visited:
            raise RuntimeError(
                f"Location cycle detected: {current.id} -> {current.parent_id}"
            )
        visited.add(current.parent_id)
        parent = _get_location(db, current.parent_id)
        if not parent:
            break  # 父节点已删
        path.append(parent)
        current = parent
    return path


def root_of(db: Session, location_id: int) -> Optional[Location]:
    path = path_to_root(db, location_id)
    return path[-1] if path else None


def depth_of(db: Session, location_id: int) -> int:
    """
    节点的深度：root=0, child=1, ...
    """
    return len(path_to_root(db, location_id)) - 1


def children_of(db: Session, parent_id: int) -> List[Location]:
    """列出直接子节点（不含孙节点）"""
    return (
        db.query(Location)
        .filter(Location.parent_id == parent_id)
        .order_by(Location.id)
        .all()
    )


def siblings_of(db: Session, location_id: int) -> List[Location]:
    """
    兄弟节点（同 parent），不含自己。

    若 location 是 root（无 parent），返回所有 root
    """
    loc = _get_location(db, location_id)
    if not loc:
        return []
    if loc.parent_id is None:
        # root 们的兄弟 = 所有 root
        return (
            db.query(Location)
            .filter(Location.parent_id.is_(None))
            .filter(Location.id != location_id)
            .order_by(Location.id)
            .all()
        )
    return (
        db.query(Location)
        .filter(Location.parent_id == loc.parent_id)
        .filter(Location.id != location_id)
        .order_by(Location.id)
        .all()
    )


def format_path(db: Session, location_id: int, separator: str = " / ") -> str:
    """
    路径可读化：path_to_root 反转 + 拼接
    例：东京 / 涩谷 / 猫头鹰咖啡馆
    """
    path = path_to_root(db, location_id)
    if not path:
        return "<unknown>"
    # 路径是 [leaf, ..., root]，反转成 [root, ..., leaf]
    return separator.join(loc.name for loc in reversed(path))


def is_descendant_of(db: Session, child_id: int, ancestor_id: int) -> bool:
    """
    判断 child 是否为 ancestor 的后代（含直接子节点）。
    """
    if child_id == ancestor_id:
        return False
    path = path_to_root(db, child_id)
    return any(loc.id == ancestor_id for loc in path)
