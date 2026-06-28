"""
WorldEngine — 世界四要素的引擎层（ADR-009）

职责：
  1) tick_world(world_id, n=1) — 推进 n 天，更新 day_of_year / season / 触发事件
  2) get_context_for_character(character_id) — 角色能感知的世界上下文
  3) get_weather_for_location(location_id, day=None) — 某地某天的天气
  4) get_recent_events(world_id, days=7) — 最近 n 天世界级事件
  5) 单例模式 + session_factory 注入（与 jiwen_manager 一致）

设计要点：
  - 测试隔离：构造函数接受 session_factory → 测试可注入 TestingSessionLocal
  - 失败不阻塞主流程：tick 失败仅日志，chat 仍可走
  - 被动查询：Director 在需要时调 get_context_for_character，不主动 push
  - 零模板侵入：返回 dict 让 Director 塞进 current_state._world（与 jiwen 同策略）

参照：
  - backend/jiwen/jiwen_manager.py — 单例 + session_factory 模式
  - backend/world/season_calendar.py — 季节/天气算法
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import (
    Character,
    Location,
    World,
    WorldEvent,
)
from backend.world.season_calendar import (
    compute_season,
    generate_weather,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 工具函数
# ============================================================================
def _normalize_pair(a: int, b: int) -> tuple:
    """关系无向图去重：保证 a < b"""
    return (min(a, b), max(a, b))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# WorldEngine
# ============================================================================
class WorldEngine:
    """
    世界引擎单例（per-process）。

    使用：
        engine = WorldEngine()                          # 生产
        engine = WorldEngine(session_factory=TestS)     # 测试
    """

    _instance: Optional["WorldEngine"] = None
    _lock = threading.Lock()

    def __new__(cls, session_factory: Optional[Callable[[], Session]] = None):
        """单例：相同 session_factory 才复用"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        if getattr(self, "_initialized", False):
            return
        self._session_factory = session_factory or SessionLocal
        self._initialized = True

    @contextmanager
    def _db(self):
        """自管理 session（与 jiwen_manager._db 一致）"""
        db = self._session_factory()
        try:
            yield db
        finally:
            db.close()

    # ----------------------------------------------------------------------
    # 推进：tick_world
    # ----------------------------------------------------------------------
    def tick_world(self, world_id: int = 1, n: int = 1) -> Dict[str, Any]:
        """
        推进世界 n 天（默认 1 天）。

        流程（每天）：
          1) day_of_year += 1；满 365 重置为 1，year += 1
          2) 季节切换检测：if season changed → 触发季节切换事件
          3) 天气更新：每个 location 重新计算（确定性，种子化）

        Returns:
            {
                "world_id": 1,
                "old_day": 31,
                "new_day": 32,
                "old_season": "spring",
                "new_season": "spring",
                "season_changed": False,
                "year_rollover": False,
                "weather_changes": [{"location_id": 1, "weather": "rainy"}],  # 简版
                "events_created": [{"id": 5, "title": "春分", "kind": "seasonal"}],
            }
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")

        result = {
            "world_id": world_id,
            "n": n,
            "old_day": None,
            "new_day": None,
            "old_season": None,
            "new_season": None,
            "season_changed": False,
            "year_rollover": False,
            "weather_changes": [],
            "events_created": [],
            "events_broadcast": 0,
        }

        with self._db() as db:
            world = db.get(World, world_id)
            if not world:
                raise ValueError(f"World {world_id} not found")

            result["old_day"] = world.day_of_year
            result["old_season"] = world.season

            for _ in range(n):
                world.day_of_year += 1
                if world.day_of_year > 365:
                    world.day_of_year = 1
                    world.year += 1
                    result["year_rollover"] = True

                # 季节重算
                new_season = compute_season(world.day_of_year, world.season_offset)
                if new_season != world.season:
                    world.season = new_season
                    result["season_changed"] = True
                    # 触发季节切换事件
                    ev = self._emit_season_event(db, world)
                    if ev:
                        result["events_created"].append({
                            "id": ev.id,
                            "title": ev.title,
                            "kind": ev.kind,
                        })
                        # [Phase 4] 广播到所有角色时间线
                        try:
                            from backend.world.relationship_network import broadcast_world_event
                            char_events = broadcast_world_event(
                                db, ev,
                                event_type="scene_event",
                            )
                            result["events_broadcast"] = len(char_events)
                        except Exception as e:
                            logger.warning("broadcast_world_event 失败: %s", e)
                            result["events_broadcast"] = 0

            result["new_day"] = world.day_of_year
            result["new_season"] = world.season

            # 天气抽样（仅返回前 5 个 location 的天气作为摘要，避免 result 爆炸）
            locations = db.query(Location).filter(Location.world_id == world_id).limit(5).all()
            for loc in locations:
                weather = generate_weather(
                    location_id=loc.id,
                    day_of_year=world.day_of_year,
                    season=world.season,
                    climate=loc.climate or "temperate",
                )
                result["weather_changes"].append({
                    "location_id": loc.id,
                    "name": loc.name,
                    "weather": weather,
                })

            db.commit()

        return result

    def _emit_season_event(
        self,
        db: Session,
        world: World,
    ) -> Optional[WorldEvent]:
        """季节切换时记录 WorldEvent"""
        season_cn = {
            "spring": "立春", "summer": "立夏",
            "fall": "立秋", "winter": "立冬",
        }
        title = season_cn.get(world.season, f"进入{world.season}")
        ev = WorldEvent(
            world_id=world.id,
            location_id=None,
            title=title,
            description=f"世界进入{world.season}季 (第{world.year}年 day {world.day_of_year})",
            kind="seasonal",
            scope="public",
            day=world.day_of_year,
            year=world.year,
        )
        db.add(ev)
        db.flush()  # 取 id
        return ev

    # ----------------------------------------------------------------------
    # 查询：角色世界上下文（Director 注入用）
    # ----------------------------------------------------------------------
    def get_context_for_character(
        self,
        character_id: int,
    ) -> Dict[str, Any]:
        """
        角色能感知的世界上下文。

        返回 dict，让 Director 塞进 current_state._world。
        失败时返回空 dict（不阻塞主流程）。

        Returns:
            {
                "world": {"id": 1, "name": "默认世界", "season": "spring", "day": 32, "year": 1},
                "location": {"id": 1, "name": "酒馆", "kind": "building", "path": "默认世界/酒馆"} | None,
                "weather": "rainy" | None,
                "recent_events": [{"id": 1, "title": "立春", "day": 60, "kind": "seasonal"}, ...],
            }
        """
        try:
            with self._db() as db:
                char = db.get(Character, character_id)
                if not char:
                    return {}

                world = None
                if char.world_id:
                    world = db.get(World, char.world_id)
                if not world:
                    return {}

                location = None
                if char.current_location_id:
                    loc = db.get(Location, char.current_location_id)
                    if loc and loc.world_id == world.id:
                        from backend.world.location_tree import format_path
                        location = {
                            "id": loc.id,
                            "name": loc.name,
                            "kind": loc.kind,
                            "path": format_path(db, loc.id),
                        }

                weather = None
                if location:
                    weather = generate_weather(
                        location_id=location["id"],
                        day_of_year=world.day_of_year,
                        season=world.season,
                        climate="temperate",
                    )

                # 最近 7 天事件（按时间倒序）
                recent = self._get_recent_events(db, world.id, days=7, limit=3)

                return {
                    "world": {
                        "id": world.id,
                        "name": world.name,
                        "season": world.season,
                        "day": world.day_of_year,
                        "year": world.year,
                    },
                    "location": location,
                    "weather": weather,
                    "recent_events": recent,
                }
        except Exception as e:
            logger.debug("WorldEngine.get_context_for_character 失败: %s", e)
            return {}

    def _get_recent_events(
        self,
        db: Session,
        world_id: int,
        days: int = 7,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """最近 N 天世界级事件"""
        world = db.get(World, world_id)
        if not world:
            return []
        # 简化：取当前年的最近事件（不跨年回溯，PoC 阶段够用）
        rows = (
            db.query(WorldEvent)
            .filter(WorldEvent.world_id == world_id)
            .filter(WorldEvent.year == world.year)
            .order_by(WorldEvent.day.desc())
            .limit(limit)
            .all()
        )
        return [
            {"id": ev.id, "title": ev.title, "day": ev.day, "kind": ev.kind}
            for ev in rows
        ]

    # ----------------------------------------------------------------------
    # 查询：天气单查
    # ----------------------------------------------------------------------
    def get_weather_for_location(
        self,
        location_id: int,
        day_of_year: Optional[int] = None,
    ) -> Optional[str]:
        """查询某地某天天气（day_of_year 不传则用当前世界 day）"""
        with self._db() as db:
            loc = db.get(Location, location_id)
            if not loc:
                return None
            if day_of_year is None:
                world = db.get(World, loc.world_id)
                if not world:
                    return None
                day_of_year = world.day_of_year
            return generate_weather(
                location_id=loc.id,
                day_of_year=day_of_year,
                season=compute_season(day_of_year, world.season_offset if (world := db.get(World, loc.world_id)) else 0),
                climate=loc.climate or "temperate",
            )

    # ----------------------------------------------------------------------
    # 辅助：列所有 worlds
    # ----------------------------------------------------------------------
    def list_worlds(self) -> List[World]:
        with self._db() as db:
            return db.query(World).order_by(World.id).all()


# ============================================================================
# 单例辅助（与 jiwen_manager.get_jiwen_manager 同模式）
# ============================================================================
_default_engine: Optional[WorldEngine] = None
_engine_lock = threading.Lock()


def get_world_engine(session_factory: Optional[Callable[[], Session]] = None) -> WorldEngine:
    """
    获取 WorldEngine 单例。

    测试可在 conftest 中 monkeypatch：
        monkeypatch.setattr(world_engine_module, "_default_engine",
                            WorldEngine(session_factory=TestingSessionLocal))
    """
    global _default_engine
    if _default_engine is None:
        with _engine_lock:
            if _default_engine is None:
                _default_engine = WorldEngine(session_factory=session_factory)
    return _default_engine


def reset_world_engine() -> None:
    """测试间清理：把单例重置（jiwen conftest 经验）"""
    global _default_engine
    with _engine_lock:
        _default_engine = None
    WorldEngine._instance = None
