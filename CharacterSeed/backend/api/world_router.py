"""
world REST API（ADR-009 / Phase 2）

Endpoints（10+）:
  Worlds
    GET    /api/worlds                       — 列出世界
    GET    /api/worlds/{wid}                 — 详情
    POST   /api/worlds                       — 创建（一般用不到，默认世界自动种子）
    PATCH  /api/worlds/{wid}                 — 改名/描述/规则
    DELETE /api/worlds/{wid}                 — 删除（仅当无角色时允许）

  World time
    GET    /api/worlds/{wid}/state           — 当前 season/day/year/season_offset
    POST   /api/worlds/{wid}/tick            — 推进 n 天（默认 1）
    GET    /api/worlds/{wid}/events          — 世界事件列表（按时间倒序）
    GET    /api/worlds/{wid}/weather         — 各地点当前天气

  Locations
    GET    /api/worlds/{wid}/locations       — 树形地点（root + 嵌套子节点）
    POST   /api/worlds/{wid}/locations       — 创建地点
    GET    /api/locations/{lid}              — 详情（含 path）
    PATCH  /api/locations/{lid}              — 改属性
    DELETE /api/locations/{lid}              — 删除（级联 SET NULL parent_id）
    POST   /api/locations/{lid}/weather      — 查某地今天的天气

  Items
    GET    /api/worlds/{wid}/items           — 物品列表（按 owner_kind 过滤）
    POST   /api/worlds/{wid}/items           — 创建物品
    GET    /api/items/{iid}                  — 详情
    PATCH  /api/items/{iid}                  — 改属性
    DELETE /api/items/{iid}                  — 删除

  Relationships
    GET    /api/characters/{cid}/relationships — 角色的关系网
    POST   /api/worlds/{wid}/relationships     — 创建关系（应用层 min/max 排序）
    PATCH  /api/relationships/{rid}            — 改 type/strength
    DELETE /api/relationships/{rid}            — 删除

  Character context
    GET    /api/characters/{cid}/world-context — 角色世界上下文（Director 注入同款）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    Character,
    Item,
    Location,
    Relationship,
    World,
    WorldEvent,
)
from backend.world.location_tree import (
    children_of,
    format_path,
    path_to_root,
)
from backend.world.season_calendar import generate_weather
from backend.world.world_engine import WorldEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["world"])


# ======================================================================
# Pydantic schemas
# ======================================================================
class WorldCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    season_offset: int = 0


class WorldPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None
    season_offset: Optional[int] = None


class LocationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    parent_id: Optional[int] = None
    kind: str = "generic"
    description: Optional[str] = None
    climate: str = "temperate"
    capacity: Optional[int] = None
    is_public: bool = True
    owner_id: Optional[int] = None


class LocationPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    parent_id: Optional[int] = None
    kind: Optional[str] = None
    description: Optional[str] = None
    climate: Optional[str] = None
    capacity: Optional[int] = None
    is_public: Optional[bool] = None
    owner_id: Optional[int] = None


class ItemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    owner_kind: str = Field(..., pattern="^(character|location|container)$")
    owner_id: int
    rarity: str = "common"
    value: int = 0
    properties_json: Optional[str] = None


class ItemPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None
    owner_kind: Optional[str] = Field(default=None, pattern="^(character|location|container)$")
    owner_id: Optional[int] = None
    rarity: Optional[str] = None
    value: Optional[int] = None
    properties_json: Optional[str] = None


class RelationshipCreate(BaseModel):
    char_a_id: int
    char_b_id: int
    type: str = Field(..., pattern="^(family|friend|lover|rival|mentor|acquaintance|enemy)$")
    strength: int = Field(0, ge=-100, le=100)


class RelationshipPatch(BaseModel):
    type: Optional[str] = Field(default=None, pattern="^(family|friend|lover|rival|mentor|acquaintance|enemy)$")
    strength: Optional[int] = Field(default=None, ge=-100, le=100)


class TickRequest(BaseModel):
    n: int = Field(1, ge=1, le=3650, description="推进天数，1-3650")


# ======================================================================
# Helpers
# ======================================================================
def _world_to_dict(w: World) -> Dict[str, Any]:
    return {
        "id": w.id,
        "name": w.name,
        "description": w.description,
        "rules_json": w.rules_json,
        "season": w.season,
        "day_of_year": w.day_of_year,
        "year": w.year,
        "season_offset": w.season_offset,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


def _location_to_dict(loc: Location, db: Session, *, with_path: bool = True) -> Dict[str, Any]:
    out = {
        "id": loc.id,
        "world_id": loc.world_id,
        "parent_id": loc.parent_id,
        "name": loc.name,
        "kind": loc.kind,
        "description": loc.description,
        "climate": loc.climate,
        "capacity": loc.capacity,
        "is_public": loc.is_public,
        "owner_id": loc.owner_id,
        "created_at": loc.created_at.isoformat() if loc.created_at else None,
    }
    if with_path:
        out["path"] = format_path(db, loc.id)
    return out


def _item_to_dict(i: Item) -> Dict[str, Any]:
    return {
        "id": i.id,
        "world_id": i.world_id,
        "name": i.name,
        "description": i.description,
        "owner_kind": i.owner_kind,
        "owner_id": i.owner_id,
        "properties_json": i.properties_json,
        "rarity": i.rarity,
        "value": i.value,
        "created_at": i.created_at.isoformat() if i.created_at else None,
    }


def _relationship_to_dict(r: Relationship) -> Dict[str, Any]:
    return {
        "id": r.id,
        "world_id": r.world_id,
        "char_a_id": r.char_a_id,
        "char_b_id": r.char_b_id,
        "type": r.type,
        "strength": r.strength,
        "history_json": r.history_json,
        "last_interaction_at": r.last_interaction_at.isoformat() if r.last_interaction_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _event_to_dict(e: WorldEvent) -> Dict[str, Any]:
    return {
        "id": e.id,
        "world_id": e.world_id,
        "location_id": e.location_id,
        "title": e.title,
        "description": e.description,
        "kind": e.kind,
        "scope": e.scope,
        "day": e.day,
        "year": e.year,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _normalize_pair(a: int, b: int) -> tuple:
    return (min(a, b), max(a, b))


def _get_world_or_404(db: Session, wid: int) -> World:
    w = db.get(World, wid)
    if not w:
        raise HTTPException(status_code=404, detail=f"World {wid} not found")
    return w


def _get_location_or_404(db: Session, lid: int) -> Location:
    loc = db.get(Location, lid)
    if not loc:
        raise HTTPException(status_code=404, detail=f"Location {lid} not found")
    return loc


def _get_item_or_404(db: Session, iid: int) -> Item:
    i = db.get(Item, iid)
    if not i:
        raise HTTPException(status_code=404, detail=f"Item {iid} not found")
    return i


def _get_relationship_or_404(db: Session, rid: int) -> Relationship:
    r = db.get(Relationship, rid)
    if not r:
        raise HTTPException(status_code=404, detail=f"Relationship {rid} not found")
    return r


# ======================================================================
# Worlds
# ======================================================================
@router.get("/worlds")
def list_worlds(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = db.query(World).order_by(World.id.asc()).all()
    return [_world_to_dict(w) for w in rows]


@router.post("/worlds", status_code=201)
def create_world(body: WorldCreate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    w = World(
        name=body.name,
        description=body.description,
        season_offset=body.season_offset,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return _world_to_dict(w)


@router.get("/worlds/{wid}")
def get_world(wid: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    w = _get_world_or_404(db, wid)
    return _world_to_dict(w)


@router.patch("/worlds/{wid}")
def patch_world(wid: int, body: WorldPatch, db: Session = Depends(get_db)) -> Dict[str, Any]:
    w = _get_world_or_404(db, wid)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(w, k, v)
    db.commit()
    db.refresh(w)
    return _world_to_dict(w)


@router.delete("/worlds/{wid}", status_code=204, response_model=None)
def delete_world(wid: int, db: Session = Depends(get_db)) -> None:
    w = _get_world_or_404(db, wid)
    # 仅当无角色时允许删除
    char_count = db.query(Character).filter(Character.world_id == wid).count()
    if char_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"World {wid} still has {char_count} characters, cannot delete",
        )
    db.delete(w)
    db.commit()


# ======================================================================
# World time / state / events / weather
# ======================================================================
@router.get("/worlds/{wid}/state")
def get_world_state(wid: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    w = _get_world_or_404(db, wid)
    return {
        "id": w.id,
        "name": w.name,
        "season": w.season,
        "day_of_year": w.day_of_year,
        "year": w.year,
        "season_offset": w.season_offset,
    }


@router.post("/worlds/{wid}/tick")
def tick_world(
    wid: int,
    body: Optional[TickRequest] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """推进 n 天（默认 1）。返回 tick 结果摘要。"""
    _get_world_or_404(db, wid)  # 404 检查
    n = (body.n if body else 1)
    engine = WorldEngine()
    try:
        return engine.tick_world(world_id=wid, n=n)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/worlds/{wid}/events")
def list_world_events(
    wid: int,
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    _get_world_or_404(db, wid)
    rows = (
        db.query(WorldEvent)
        .filter(WorldEvent.world_id == wid)
        .order_by(WorldEvent.year.desc(), WorldEvent.day.desc(), WorldEvent.id.desc())
        .limit(limit)
        .all()
    )
    return [_event_to_dict(e) for e in rows]


@router.get("/worlds/{wid}/weather")
def list_weather(wid: int, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    w = _get_world_or_404(db, wid)
    locs = (
        db.query(Location)
        .filter(Location.world_id == wid)
        .order_by(Location.id.asc())
        .all()
    )
    out: List[Dict[str, Any]] = []
    for loc in locs:
        weather = generate_weather(
            location_id=loc.id,
            day_of_year=w.day_of_year,
            season=w.season,
            climate=loc.climate or "temperate",
        )
        out.append({
            "location_id": loc.id,
            "name": loc.name,
            "kind": loc.kind,
            "climate": loc.climate,
            "weather": weather,
        })
    return out


# ======================================================================
# Locations
# ======================================================================
@router.get("/worlds/{wid}/locations")
def list_locations(wid: int, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    _get_world_or_404(db, wid)
    # 返回所有 location（树形结构由前端组装；这里给扁平列表 + path）
    rows = (
        db.query(Location)
        .filter(Location.world_id == wid)
        .order_by(Location.parent_id.asc().nulls_first(), Location.id.asc())
        .all()
    )
    return [_location_to_dict(loc, db) for loc in rows]


@router.post("/worlds/{wid}/locations", status_code=201)
def create_location(
    wid: int,
    body: LocationCreate,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _get_world_or_404(db, wid)
    if body.parent_id is not None:
        parent = db.get(Location, body.parent_id)
        if not parent or parent.world_id != wid:
            raise HTTPException(status_code=400, detail=f"parent_id {body.parent_id} not in world {wid}")
    if body.owner_id is not None:
        owner = db.get(Character, body.owner_id)
        if not owner:
            raise HTTPException(status_code=400, detail=f"owner_id {body.owner_id} not found")
    loc = Location(
        world_id=wid,
        parent_id=body.parent_id,
        name=body.name,
        kind=body.kind,
        description=body.description,
        climate=body.climate,
        capacity=body.capacity,
        is_public=body.is_public,
        owner_id=body.owner_id,
    )
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return _location_to_dict(loc, db)


@router.get("/locations/{lid}")
def get_location(lid: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    loc = _get_location_or_404(db, lid)
    return _location_to_dict(loc, db)


@router.patch("/locations/{lid}")
def patch_location(lid: int, body: LocationPatch, db: Session = Depends(get_db)) -> Dict[str, Any]:
    loc = _get_location_or_404(db, lid)
    data = body.model_dump(exclude_unset=True)
    if "parent_id" in data:
        if data["parent_id"] == loc.id:
            raise HTTPException(status_code=400, detail="parent_id cannot equal id")
        if data["parent_id"] is not None:
            parent = db.get(Location, data["parent_id"])
            if not parent or parent.world_id != loc.world_id:
                raise HTTPException(status_code=400, detail="parent_id not in same world")
    if "owner_id" in data and data["owner_id"] is not None:
        owner = db.get(Character, data["owner_id"])
        if not owner:
            raise HTTPException(status_code=400, detail="owner_id not found")
    for k, v in data.items():
        setattr(loc, k, v)
    db.commit()
    db.refresh(loc)
    return _location_to_dict(loc, db)


@router.delete("/locations/{lid}", status_code=204, response_model=None)
def delete_location(lid: int, db: Session = Depends(get_db)) -> None:
    loc = _get_location_or_404(db, lid)
    # 1) 把子节点的 parent_id 设为 NULL（依赖外键 ON DELETE SET NULL 也可）
    db.query(Location).filter(Location.parent_id == lid).update({"parent_id": None})
    # 2) 把停在该地的角色 current_location_id 设为 NULL
    db.query(Character).filter(Character.current_location_id == lid).update({"current_location_id": None})
    # 3) 删除
    db.delete(loc)
    db.commit()


@router.get("/locations/{lid}/weather")
def get_location_weather(
    lid: int,
    day: Optional[int] = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    loc = _get_location_or_404(db, lid)
    w = db.get(World, loc.world_id)
    if not w:
        raise HTTPException(status_code=500, detail="World not found for location")
    target_day = day if day is not None else w.day_of_year
    # 如果指定了 day，按 day 重算 season；否则用 world 当前 season
    if day is not None:
        from backend.world.season_calendar import compute_season
        target_season = compute_season(target_day, w.season_offset)
    else:
        target_season = w.season
    weather = generate_weather(
        location_id=loc.id,
        day_of_year=target_day,
        season=target_season,
        climate=loc.climate or "temperate",
    )
    return {
        "location_id": loc.id,
        "name": loc.name,
        "day": target_day,
        "season": target_season,
        "climate": loc.climate,
        "weather": weather,
    }


# ======================================================================
# Items
# ======================================================================
@router.get("/worlds/{wid}/items")
def list_items(
    wid: int,
    owner_kind: Optional[str] = Query(None, pattern="^(character|location|container)$"),
    owner_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    _get_world_or_404(db, wid)
    q = db.query(Item).filter(Item.world_id == wid)
    if owner_kind:
        q = q.filter(Item.owner_kind == owner_kind)
    if owner_id is not None:
        q = q.filter(Item.owner_id == owner_id)
    rows = q.order_by(Item.id.asc()).all()
    return [_item_to_dict(i) for i in rows]


@router.post("/worlds/{wid}/items", status_code=201)
def create_item(wid: int, body: ItemCreate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _get_world_or_404(db, wid)
    if body.owner_kind == "character":
        if not db.get(Character, body.owner_id):
            raise HTTPException(status_code=400, detail=f"owner_id {body.owner_id} character not found")
    elif body.owner_kind == "location":
        loc = db.get(Location, body.owner_id)
        if not loc or loc.world_id != wid:
            raise HTTPException(status_code=400, detail=f"owner_id {body.owner_id} location not in world {wid}")
    i = Item(
        world_id=wid,
        name=body.name,
        description=body.description,
        owner_kind=body.owner_kind,
        owner_id=body.owner_id,
        rarity=body.rarity,
        value=body.value,
        properties_json=body.properties_json,
    )
    db.add(i)
    db.commit()
    db.refresh(i)
    return _item_to_dict(i)


@router.get("/items/{iid}")
def get_item(iid: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    i = _get_item_or_404(db, iid)
    return _item_to_dict(i)


@router.patch("/items/{iid}")
def patch_item(iid: int, body: ItemPatch, db: Session = Depends(get_db)) -> Dict[str, Any]:
    i = _get_item_or_404(db, iid)
    data = body.model_dump(exclude_unset=True)
    if "owner_kind" in data or "owner_id" in data:
        new_kind = data.get("owner_kind", i.owner_kind)
        new_id = data.get("owner_id", i.owner_id)
        if new_kind == "character":
            if not db.get(Character, new_id):
                raise HTTPException(status_code=400, detail="owner character not found")
        elif new_kind == "location":
            loc = db.get(Location, new_id)
            if not loc or loc.world_id != i.world_id:
                raise HTTPException(status_code=400, detail="owner location not in same world")
    for k, v in data.items():
        setattr(i, k, v)
    db.commit()
    db.refresh(i)
    return _item_to_dict(i)


@router.delete("/items/{iid}", status_code=204, response_model=None)
def delete_item(iid: int, db: Session = Depends(get_db)) -> None:
    i = _get_item_or_404(db, iid)
    db.delete(i)
    db.commit()


# ======================================================================
# Relationships
# ======================================================================
@router.get("/characters/{cid}/relationships")
def list_character_relationships(cid: int, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    if not db.get(Character, cid):
        raise HTTPException(status_code=404, detail=f"Character {cid} not found")
    rows = (
        db.query(Relationship)
        .filter((Relationship.char_a_id == cid) | (Relationship.char_b_id == cid))
        .order_by(Relationship.id.asc())
        .all()
    )
    return [_relationship_to_dict(r) for r in rows]


@router.post("/worlds/{wid}/relationships", status_code=201)
def create_relationship(
    wid: int,
    body: RelationshipCreate,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _get_world_or_404(db, wid)
    if body.char_a_id == body.char_b_id:
        raise HTTPException(status_code=400, detail="char_a_id == char_b_id")
    for cid in (body.char_a_id, body.char_b_id):
        if not db.get(Character, cid):
            raise HTTPException(status_code=400, detail=f"character {cid} not found")
    a, b = _normalize_pair(body.char_a_id, body.char_b_id)
    # 检查已存在
    existing = (
        db.query(Relationship)
        .filter(Relationship.char_a_id == a, Relationship.char_b_id == b)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"relationship ({a},{b}) already exists")
    r = Relationship(
        world_id=wid,
        char_a_id=a,
        char_b_id=b,
        type=body.type,
        strength=body.strength,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _relationship_to_dict(r)


@router.patch("/relationships/{rid}")
def patch_relationship(
    rid: int,
    body: RelationshipPatch,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    r = _get_relationship_or_404(db, rid)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    r.last_interaction_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(r)
    return _relationship_to_dict(r)


@router.delete("/relationships/{rid}", status_code=204, response_model=None)
def delete_relationship(rid: int, db: Session = Depends(get_db)) -> None:
    r = _get_relationship_or_404(db, rid)
    db.delete(r)
    db.commit()


# ======================================================================
# Character world context
# ======================================================================
@router.get("/characters/{cid}/world-context")
def get_character_world_context(cid: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    角色当前的世界上下文：world / location / weather / recent_events
    与 WorldEngine.get_context_for_character 一致。
    """
    if not db.get(Character, cid):
        raise HTTPException(status_code=404, detail=f"Character {cid} not found")
    engine = WorldEngine()
    return engine.get_context_for_character(cid)


# ======================================================================
# [Phase 4] Relationship graph + Director injection preview
# ======================================================================
@router.get("/worlds/{wid}/relationship-graph")
def get_world_relationship_graph(wid: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    返回世界的关系网图（节点 + 边）— 供前端 SVG 可视化。

    节点：每个角色（id + name）
    边：每条关系（from + to + type + strength；归一化后 from < to）
    """
    if not db.get(World, wid):
        raise HTTPException(status_code=404, detail=f"World {wid} not found")

    from backend.models import Character
    chars = db.query(Character).filter(Character.world_id == wid).all()
    nodes = [{"id": c.id, "name": c.name} for c in chars]

    edges_rows = (
        db.query(Relationship)
        .filter(Relationship.world_id == wid)
        .order_by(Relationship.strength.desc())
        .all()
    )
    edges = [
        {
            "id": r.id,
            "source": r.char_a_id,
            "target": r.char_b_id,
            "type": r.type,
            "strength": r.strength,
        }
        for r in edges_rows
    ]
    return {
        "world_id": wid,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "character_count": len(nodes),
            "relationship_count": len(edges),
        },
    }


@router.get("/characters/{cid}/relationships/preview")
def get_relationship_injection_preview(cid: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    [Phase 4] Director prompt 注入的关系网预览（前端调试用）。

    返回 build_relationship_subfield 的实际结果：
      - top_relationships（strength 最高 5 条）
      - recent_changes（窗口内变化）
    """
    if not db.get(Character, cid):
        raise HTTPException(status_code=404, detail=f"Character {cid} not found")
    from backend.world.relationship_network import build_relationship_subfield
    sub = build_relationship_subfield(cid)
    return sub or {"summary": "", "top_relationships": [], "recent_changes": []}


class BroadcastRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    kind: str = Field(default="global", pattern="^(global|local|seasonal|weather)$")
    location_id: Optional[int] = None


@router.post("/worlds/{wid}/broadcast-event", status_code=201)
def broadcast_world_event_endpoint(
    wid: int,
    body: BroadcastRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    [Phase 4] 手动创建 WorldEvent + 广播到所有角色时间线。

    例：用户在前端点击"播报节日" → 此端点创建 WorldEvent + 给所有角色 timeline 加 Event 行
    """
    from backend.models import WorldEvent
    if not db.get(World, wid):
        raise HTTPException(status_code=404, detail=f"World {wid} not found")

    world = db.get(World, wid)
    wev = WorldEvent(
        world_id=wid,
        location_id=body.location_id,
        title=body.title,
        description=body.description,
        kind=body.kind,
        scope="public",
        day=world.day_of_year,
        year=world.year,
    )
    db.add(wev)
    db.commit()
    db.refresh(wev)

    # 广播
    from backend.world.relationship_network import broadcast_world_event
    char_events = broadcast_world_event(db, wev, event_type="scene_event")
    return {
        "world_event": _event_to_dict(wev),
        "broadcast_count": len(char_events),
    }
