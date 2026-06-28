"""
Relationship Network — Phase 4 关系网 + Director 注入（ADR-009 / §3.5）

职责：
  1. **关系查询**（`get_relationships_of`）：
     - 角色为中心，查所有相关关系（char_a OR char_b == cid）
     - 按强度排序，返回结构化数据

  2. **关系演化检测**（`get_recent_relationship_changes`）：
     - 对比 strength / last_interaction_at
     - 输出"你最近和 ta 关系变差了/变好了"

  3. **Director 注入**（`build_relationship_subfield`）：
     - 把关系网塞进 current_state._world._relationships
     - 零模板侵入（与 jiwen / location_aware 同策略）

  4. **跨角色事件 broadcast**（`broadcast_world_event`）：
     - WorldEvent → 给同 world 所有角色生成 Event 行
     - 让每个角色的"事件时间线"都能感知世界级变化
     - 例：tick_world 触发"立春" → 3 个角色 timeline 各加一条

设计原则：
  - **读多写少**：关系查询是 LLM prompt 注入的关键路径
  - **失败静默**：与 build_world_subfield 一致，抛异常时返回 None
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ======================================================================
# 关系查询
# ======================================================================
def get_relationships_of(
    db: Session,
    character_id: int,
    *,
    world_id: Optional[int] = None,
    include_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    查询某角色所有相关关系。

    实现：
      - WHERE (char_a_id = :cid OR char_b_id = :cid)
      - 包含"另一方"的角色 ID + 名字（join Character）
      - 按 strength DESC 排序

    Args:
        character_id: 中心角色
        world_id: 限定到某个 world（None = 全部，跨 world）
        include_types: 限定关系类型（['friend','lover']），None = 全部

    Returns:
        [
            {
                "relationship_id": 1,
                "world_id": 1,
                "type": "friend",
                "strength": 75,
                "other_character_id": 3,
                "other_character_name": "陆远",
                "last_interaction_at": "2026-06-25T...",
            },
            ...
        ]
    """
    from backend.models import Character, Relationship

    q = db.query(Relationship).filter(
        (Relationship.char_a_id == character_id) | (Relationship.char_b_id == character_id)
    )
    if world_id is not None:
        q = q.filter(Relationship.world_id == world_id)
    if include_types:
        q = q.filter(Relationship.type.in_(include_types))

    rows = q.order_by(Relationship.strength.desc()).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        # 确定"另一方"
        if r.char_a_id == character_id:
            other_id = r.char_b_id
        else:
            other_id = r.char_a_id
        other = db.get(Character, other_id)
        out.append({
            "relationship_id": r.id,
            "world_id": r.world_id,
            "type": r.type,
            "strength": r.strength,
            "other_character_id": other_id,
            "other_character_name": other.name if other else f"(id={other_id})",
            "last_interaction_at": r.last_interaction_at.isoformat() if r.last_interaction_at else None,
        })
    return out


# ======================================================================
# 关系演化检测
# ======================================================================
def detect_relationship_changes(
    db: Session,
    character_id: int,
    *,
    window_days: int = 7,
    threshold: int = 10,
) -> List[Dict[str, Any]]:
    """
    检测关系强度近期变化（基于 last_interaction_at + history_json）。

    简化版：直接读 relationship.history_json（如果有），列出"最近 N 天"的 delta 事件。
    真实业务可以接 GrowthService 写入 history。

    Args:
        character_id: 中心角色
        window_days: 时间窗口（默认 7 天）
        threshold: 强度变化阈值（小于此值不报）

    Returns:
        [
            {
                "other_character_id": 3,
                "other_character_name": "陆远",
                "type": "rival",
                "strength_now": -30,
                "delta": -25,
                "summary": "你最近和 ta 关系变差了",
            },
            ...
        ]
    """
    import json
    from datetime import datetime, timedelta, timezone

    rels = get_relationships_of(db, character_id)
    if not rels:
        return []
    out: List[Dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    for r in rels:
        # 查完整 Relationship 行（要 history_json）
        from backend.models import Relationship
        full = db.get(Relationship, r["relationship_id"])
        if not full or not full.history_json:
            continue
        try:
            history = json.loads(full.history_json)
        except Exception:
            continue
        if not isinstance(history, list):
            continue
        # 收集窗口内的 delta
        recent = []
        for h in history:
            ts = h.get("ts")
            if not ts:
                continue
            try:
                h_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if h_dt >= cutoff:
                recent.append(h)
        if not recent:
            continue
        total_delta = sum(int(h.get("delta", 0)) for h in recent)
        if abs(total_delta) < threshold:
            continue
        # 拼 summary
        direction = "变好了" if total_delta > 0 else "变差了"
        out.append({
            "other_character_id": r["other_character_id"],
            "other_character_name": r["other_character_name"],
            "type": r["type"],
            "strength_now": r["strength"],
            "delta": total_delta,
            "summary": f"你最近和 {r['other_character_name']} 关系{direction}",
        })
    return out


# ======================================================================
# Director 注入
# ======================================================================
def build_relationship_subfield(
    character_id: int,
    engine=None,
) -> Optional[Dict[str, Any]]:
    """
    构造 current_state._world._relationships 子字段。

    失败静默：返回 None（不影响主流程）。

    Args:
        character_id: 角色 ID
        engine: 可选 WorldEngine 实例（默认懒加载单例）

    Returns:
        Dict 形如：
        {
            "summary": "你和 3 个角色有关系；最近和 陆远 关系变差了",
            "top_relationships": [...],  # strength 最高 5 条
            "recent_changes": [...],     # 窗口内的演化
        }
        或 None
    """
    try:
        if engine is None:
            from backend.world.world_engine import get_world_engine
            engine = get_world_engine()
        # 复用 engine._db（单例 + session_factory 注入）
        with engine._db() as db:
            rels = get_relationships_of(db, character_id)
            changes = detect_relationship_changes(db, character_id)
        if not rels and not changes:
            return None

        # 拼 summary：关系数 + 类型分布 + 演化
        parts = []
        if rels:
            # 按 type 统计
            type_counts: Dict[str, int] = {}
            for r in rels:
                type_counts[r["type"]] = type_counts.get(r["type"], 0) + 1
            type_summary = "、".join(f"{k} {v}" for k, v in type_counts.items())
            parts.append(f"你和 {len(rels)} 个角色有关系（{type_summary}）")
        if changes:
            change_summaries = "；".join(c["summary"] for c in changes[:3])
            parts.append(f"最近：{change_summaries}")
        return {
            "summary": "；".join(parts) if parts else "",
            "top_relationships": rels[:5],
            "recent_changes": changes,
        }
    except Exception as e:
        logger.debug("build_relationship_subfield 失败: %s", e)
        return None


# ======================================================================
# 跨角色事件 broadcast
# ======================================================================
def broadcast_world_event(
    db: Session,
    world_event: Any,
    *,
    event_type: str = "scene_event",
    time_period: Optional[str] = None,
    content_template: Optional[str] = None,
) -> List[Any]:
    """
    把 WorldEvent 广播给同 world 的所有角色（写 Event 行）。

    Args:
        db: SQLAlchemy session（与 WorldEvent 同一连接，方便 commit）
        world_event: WorldEvent ORM 实例
        event_type: 角色 Event 的类型（默认 scene_event）
        time_period: 时段（morning/afternoon/evening/night）
        content_template: 模板字符串，{title} {description} {location_name} 可被替换

    Returns:
        创建的 Event 行列表
    """
    from backend.models import Character, Event

    # 1) 找同 world 的所有角色
    chars = (
        db.query(Character)
        .filter(Character.world_id == world_event.world_id)
        .all()
    )
    if not chars:
        return []

    # 2) 找 location 名字（可选）
    location_name = None
    if world_event.location_id:
        from backend.models import Location
        loc = db.get(Location, world_event.location_id)
        if loc:
            location_name = loc.name

    # 3) 为每个角色生成 Event
    created = []
    for ch in chars:
        if content_template:
            content = content_template.format(
                title=world_event.title,
                description=world_event.description or "",
                location_name=location_name or "",
                character_name=ch.name,
            )
        else:
            # 默认：世界级事件 + 角色名字
            content = f"[{world_event.title}] {ch.name} 也感受到了这股变化"
            if world_event.description:
                content += f"（{world_event.description}）"
        ev = Event(
            character_id=ch.id,
            day_number=world_event.day,  # 同步到角色时间线
            order_index=0,
            event_type=event_type,
            content=content,
            status="completed",  # 视为已发生（被动接收）
            time_period=time_period,
        )
        db.add(ev)
        created.append(ev)
    db.commit()
    logger.info(
        "broadcast_world_event: world_event_id=%d → %d 个角色 Event",
        world_event.id, len(created),
    )
    return created
