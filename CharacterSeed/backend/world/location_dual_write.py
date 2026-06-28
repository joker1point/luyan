"""
Location Dual-Write — Phase 3 迁移 + 兼容层（ADR-009 / §4）

职责：
  1. **写时双写**（`set_character_location`）：
     - 优先用外键 location_id；没有就给 location_name 找/建 Location 行
     - 同步把 location 名字写进 current_state["location"]（兼容老查询）
     - 幂等：相同输入多次调用结果一致

  2. **读时兼容**（`get_character_location_label`）：
     - 优先返回 Location 行（结构化）
     - NULL 时回退 current_state["location"] 字符串（兼容老数据）

  3. **批量迁移**（`backfill_location_strings`）：
     - v004 migration 钩子调用，把历史 current_state["location"] 字符串
       → Location 行 → current_location_id 外键
     - 保留 current_state["location"] 字符串（双写期），等 Phase 5 再清空

设计原则：
  - **不破坏老数据**：永远不修改 current_state 现有字段，只在末尾追加
  - **幂等**：可重复跑，already_migrated 的角色会被跳过
  - **失败隔离**：单条失败不让整批回滚
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ======================================================================
# 写：双写 helper
# ======================================================================
def set_character_location(
    db: Session,
    character: Any,
    *,
    location_id: Optional[int] = None,
    location_name: Optional[str] = None,
    world_id: Optional[int] = None,
) -> Optional[Any]:
    """
    设置角色的当前位置（双写：外键 + 字符串）。

    Args:
        db: SQLAlchemy session
        character: Character ORM 实例
        location_id: 直接用 Location.id（最优先）
        location_name: 用名字找/建 Location（同 world 下 kind=generic）
        world_id: 当 location_name 非空时使用；缺省取 character.world_id

    Returns:
        Location ORM 实例；若两个参数都为空，返回 None（清空 location）

    Raises:
        ValueError: 仅给 location_name 但角色无 world_id 且未传 world_id
    """
    target = _resolve_location(
        db, location_id=location_id, location_name=location_name,
        world_id=world_id or getattr(character, "world_id", None),
    )
    # 1) 外键直接 set（不会触发 JSON 序列化）
    character.current_location_id = target.id if target else None

    # 2) 字符串双写：用 update_character 走 CRUD 层（自动 dict→JSON 序列化）
    # 这里不能直接 setattr(character, "current_state", dict) —— SQLAlchemy
    # 没有 JSONType TypeDecorator，dict 不能直接 bind 到 SQLite TEXT 参数
    # 错误示例：Error binding parameter 1: type 'dict' is not supported
    new_state = _ensure_current_state_dict(character)
    if target is not None:
        try:
            from backend.world.location_tree import format_path
            label = format_path(db, target.id) or target.name
        except Exception:
            label = target.name
        new_state["location"] = label
    else:
        new_state.pop("location", None)

    # 走 CRUD 路径让 dict 序列化为 JSON 字符串
    from backend.crud.character import update_character
    update_character(
        db, character.id,
        current_state=new_state,
        current_location_id=character.current_location_id,
    )
    return target


def _resolve_location(
    db: Session,
    *,
    location_id: Optional[int],
    location_name: Optional[str],
    world_id: Optional[int],
) -> Optional[Any]:
    """
    解析 Location：优先 location_id，否则按 name 在同 world 下查找/创建。
    """
    from backend.models import Location  # 延迟 import 避免循环

    if location_id is not None:
        loc = db.get(Location, location_id)
        if not loc:
            raise ValueError(f"Location id={location_id} not found")
        return loc
    if not location_name:
        return None
    if not world_id:
        raise ValueError(
            "set_character_location: location_name given but world_id is None "
            "(角色未绑定世界，无法创建/查找 location)"
        )
    # 同 world 下查同名
    loc = (
        db.query(Location)
        .filter(Location.world_id == world_id, Location.name == location_name)
        .first()
    )
    if loc:
        return loc
    # 没找到 → 创建一个 generic 节点
    loc = Location(
        world_id=world_id,
        name=location_name,
        kind="generic",
        climate="temperate",
    )
    db.add(loc)
    db.flush()  # 拿 id
    logger.info("set_character_location: 自动创建 Location id=%d name=%r (world_id=%d)", loc.id, location_name, world_id)
    return loc


# ======================================================================
# 读：兼容 helper
# ======================================================================
def get_character_location_label(db: Session, character: Any) -> Optional[str]:
    """
    返回角色的位置可读标签（外键优先，字符串兜底）。

    返回：
      - 有外键 → "format_path"（如 "东京 / 涩谷"）
      - 无外键但 current_state["location"] 非空 → 返回该字符串
      - 都无 → None
    """
    lid = getattr(character, "current_location_id", None)
    if lid:
        from backend.models import Location
        loc = db.get(Location, lid)
        if loc:
            try:
                from backend.world.location_tree import format_path
                return format_path(db, loc.id) or loc.name
            except Exception:
                return loc.name
    # 回退字符串
    state = _parse_current_state(character)
    return state.get("location")


def get_character_location_row(db: Session, character: Any) -> Optional[Any]:
    """返回 Location ORM 行（仅外键，字符串不返回）。"""
    lid = getattr(character, "current_location_id", None)
    if not lid:
        return None
    from backend.models import Location
    return db.get(Location, lid)


# ======================================================================
# 内部：current_state dict 安全访问
# ======================================================================
def _parse_current_state(character: Any) -> Dict[str, Any]:
    raw = getattr(character, "current_state", None) or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _ensure_current_state_dict(character: Any) -> Dict[str, Any]:
    """保证 character.current_state 是 dict（必要时 parse 字符串 → dict）"""
    raw = getattr(character, "current_state", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                # 写回 ORM（保持数据一致）
                character.current_state = d
                return d
        except Exception:
            pass
    # 兜底：新建
    new: Dict[str, Any] = {}
    character.current_state = new
    return new


# ======================================================================
# 批量迁移：current_state["location"] 字符串 → Location 外键
# ======================================================================
def backfill_location_strings(
    db: Session,
    *,
    default_world_id: int = 1,
) -> Dict[str, int]:
    """
    把所有 character.current_state["location"] 字符串迁移到 Location 外键。

    算法：
      1. 找所有 (current_state.location IS NOT NULL AND current_location_id IS NULL) 的角色
      2. 对每条：
         a. 解析字符串为 location_name（截断到 100 字符；超长时取前 100）
         b. 同 world 下查同名 Location
         c. 没找到就建一个 kind=generic, climate=temperate 的节点
         d. character.current_location_id = loc.id
         e. 保留 current_state["location"] 字符串（双写期兼容）
      3. 单条失败 → 记录但不让整批回滚

    Returns:
        {"scanned": int, "migrated": int, "skipped": int, "errors": int}
    """
    from backend.models import Character, Location

    result = {"scanned": 0, "migrated": 0, "skipped": 0, "errors": 0}

    # 1) 找目标角色（SQLite + JSON 字段为字符串 → 用 LIKE 简化筛选）
    # 兼容 current_state 为 dict 或 string
    candidates: List[Character] = []
    rows = (
        db.query(Character)
        .filter(Character.current_location_id.is_(None))
        .all()
    )
    for ch in rows:
        state = _parse_current_state(ch)
        if state.get("location"):
            candidates.append(ch)
    result["scanned"] = len(rows)
    if not candidates:
        return result

    for ch in candidates:
        try:
            state = _parse_current_state(ch)
            raw = state.get("location")
            if not raw or not isinstance(raw, str):
                result["skipped"] += 1
                continue
            name = raw.strip()[:100]
            if not name:
                result["skipped"] += 1
                continue
            wid = ch.world_id or default_world_id
            # 查/建
            loc = (
                db.query(Location)
                .filter(Location.world_id == wid, Location.name == name)
                .first()
            )
            if not loc:
                loc = Location(
                    world_id=wid,
                    name=name,
                    kind="generic",
                    climate="temperate",
                )
                db.add(loc)
                db.flush()
            ch.current_location_id = loc.id
            # 保留 current_state["location"] 不动（双写期兼容）
            result["migrated"] += 1
        except Exception as e:
            logger.warning("backfill_location_strings: cid=%d 失败: %s", getattr(ch, "id", -1), e)
            result["errors"] += 1

    db.commit()
    logger.info(
        "backfill_location_strings 完成: scanned=%d migrated=%d skipped=%d errors=%d",
        result["scanned"], result["migrated"], result["skipped"], result["errors"],
    )
    return result


# ======================================================================
# SQL-only 入口（给 v004 migration 钩子调用，绕开 ORM）
# ======================================================================
def backfill_location_strings_sqlite(engine: Engine, *, default_world_id: int = 1) -> Dict[str, int]:
    """
    用纯 SQL 实现 backfill（避免 ORM 跨 dialect 兼容问题）。

    仅支持 SQLite（character_seed.db）。
    逻辑与 backfill_location_strings 相同，但用 JSON_EXTRACT / json_each 等 SQLite 原生函数。

    重要：current_state 在 SQLite 中是 TEXT，存的是 JSON 字符串。
          用 `json_extract(current_state, '$.location')` 提取。
    """
    result = {"scanned": 0, "migrated": 0, "skipped": 0, "errors": 0}

    if not engine.url.get_backend_name().startswith("sqlite"):
        logger.warning("backfill_location_strings_sqlite: 仅支持 SQLite，%s 跳过", engine.url.get_backend_name())
        return result

    with engine.begin() as conn:
        # 1) 找 candidates: current_state JSON 中 location 非空 + current_location_id IS NULL
        rows = conn.execute(text(
            "SELECT id, world_id, json_extract(current_state, '$.location') "
            "FROM characters "
            "WHERE current_location_id IS NULL "
            "  AND json_extract(current_state, '$.location') IS NOT NULL "
            "  AND TRIM(json_extract(current_state, '$.location')) != ''"
        )).fetchall()
        result["scanned"] = len(rows)
        if not rows:
            return result

        for cid, wid, loc_name in rows:
            try:
                if not isinstance(loc_name, str):
                    result["skipped"] += 1
                    continue
                name = loc_name.strip()[:100]
                if not name:
                    result["skipped"] += 1
                    continue
                use_wid = wid if wid else default_world_id
                # 2) 查/建 Location
                loc_row = conn.execute(text(
                    "SELECT id FROM locations WHERE world_id = :wid AND name = :n"
                ), {"wid": use_wid, "n": name}).fetchone()
                if loc_row:
                    loc_id = loc_row[0]
                else:
                    res = conn.execute(text(
                        "INSERT INTO locations (world_id, name, kind, climate, is_public) "
                        "VALUES (:wid, :n, 'generic', 'temperate', 1)"
                    ), {"wid": use_wid, "n": name})
                    loc_id = res.lastrowid
                # 3) 回填外键
                conn.execute(text(
                    "UPDATE characters SET current_location_id = :lid WHERE id = :cid"
                ), {"lid": loc_id, "cid": cid})
                result["migrated"] += 1
            except Exception as e:
                logger.warning("backfill_location_strings_sqlite: cid=%d 失败: %s", cid, e)
                result["errors"] += 1

    logger.info(
        "backfill_location_strings_sqlite 完成: scanned=%d migrated=%d skipped=%d errors=%d",
        result["scanned"], result["migrated"], result["skipped"], result["errors"],
    )
    return result
