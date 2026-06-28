"""
WorldEngine + season_calendar 单元测试（ADR-009 / Phase 1）

覆盖：
  - season_calendar.compute_season（边界 + 南半球）
  - season_calendar.generate_weather（确定性）
  - WorldEngine.tick_world（推进、季节切换、年跨）
  - WorldEngine.get_context_for_character（角色上下文）
  - WorldEngine.get_weather_for_location（单查）
  - WorldEngine.list_worlds
  - 种子默认世界（v003 migration 后）
"""
from __future__ import annotations

import pytest

from backend.world.season_calendar import (
    SEASONS,
    WEATHERS,
    compute_season,
    day_to_season_change,
    generate_weather,
)
from backend.world.world_engine import (
    WorldEngine,
    get_world_engine,
    reset_world_engine,
)
from backend.models import Location, World, WorldEvent, Character


# ============================================================
# season_calendar.compute_season
# ============================================================
class TestComputeSeason:
    """北半球默认（season_offset=0）的季节边界"""

    @pytest.mark.parametrize("day,expected", [
        (1, "winter"),
        (59, "winter"),
        (60, "spring"),  # 立春边界
        (61, "spring"),
        (150, "spring"),
        (151, "summer"),  # 立夏
        (240, "summer"),
        (241, "fall"),    # 立秋
        (330, "fall"),
        (331, "winter"),  # 立冬
        (365, "winter"),
    ])
    def test_northern_hemisphere_boundaries(self, day, expected):
        assert compute_season(day, 0) == expected

    def test_invalid_day(self):
        with pytest.raises(ValueError):
            compute_season(0, 0)
        with pytest.raises(ValueError):
            compute_season(366, 0)
        with pytest.raises(ValueError):
            compute_season(-5, 0)

    def test_all_365_days_return_valid_season(self):
        """穷举：365 天每天都应返回 SEASONS 之一"""
        for day in range(1, 366):
            s = compute_season(day, 0)
            assert s in SEASONS, f"day={day} → {s}"

    def test_southern_hemisphere_offset_180(self):
        """南半球（offset=180）：季节翻转（夏→冬）"""
        # 原本 day=151 是 summer（北半球），南半球应翻成 winter
        assert compute_season(151, 180) == "winter"
        # 原本 day=60 是 spring，南半球应翻成 fall
        assert compute_season(60, 180) == "fall"

    def test_seasons_constant_complete(self):
        assert set(SEASONS) == {"spring", "summer", "fall", "winter"}
        assert len(SEASONS) == 4

    def test_day_to_season_change_signature(self):
        """day_to_season_change 返回 (changed, new_season)"""
        # mock 一个 world（必须有 season_offset 属性，day_to_season_change 会读它）
        class FakeWorld:
            season = "winter"
            season_offset = 0
        fw = FakeWorld()
        # day=60 是 spring，从 winter → spring，changed=True
        changed, new_season = day_to_season_change(fw, 60)
        assert changed is True
        assert new_season == "spring"
        # 模拟 fw.season 已被更新为新季节 → 再次调用应 False
        fw.season = new_season
        changed2, new_season2 = day_to_season_change(fw, 60)
        assert changed2 is False


# ============================================================
# season_calendar.generate_weather
# ============================================================
class TestGenerateWeather:
    """天气确定性：同一天同地点多次调用结果一致"""

    def test_returns_valid_weather(self):
        for season in SEASONS:
            w = generate_weather(1, 100, season, "temperate")
            assert w in WEATHERS

    def test_deterministic_for_same_inputs(self):
        """关键：同 (loc, day, season) 多次调用结果一致"""
        w1 = generate_weather(42, 100, "spring")
        w2 = generate_weather(42, 100, "spring")
        w3 = generate_weather(42, 100, "spring")
        assert w1 == w2 == w3

    def test_different_locations_can_have_different_weather(self):
        """不同 location 在同一天可能有不同天气（统计上）"""
        weathers = set()
        for loc_id in range(1, 30):
            weathers.add(generate_weather(loc_id, 100, "spring"))
        # 30 个 location 至少应有 2 种不同天气
        assert len(weathers) >= 2

    def test_different_seasons_produce_different_distribution(self):
        """季节影响天气概率分布（抽样 1000 次应能体现差异）"""
        from collections import Counter
        snow_count = 0
        for loc in range(1, 200):
            if generate_weather(loc, 100, "winter") == "snowy":
                snow_count += 1
        # 冬季 200 个 loc 中应至少有一些 snowy
        assert snow_count > 20  # winter 表 snowy=0.40, 期望 ~80

    def test_invalid_season_falls_back_to_spring(self):
        """未知 season 兜底为 spring（不应抛异常）"""
        w = generate_weather(1, 100, "unknown_season")
        assert w in WEATHERS

    def test_summer_more_sunny_than_winter(self):
        """夏季 sunny 比例高于冬季（概率表硬编码约束）"""
        from collections import Counter
        summer_sunny = sum(
            1 for loc in range(1, 500)
            if generate_weather(loc, 150, "summer") == "sunny"
        )
        winter_sunny = sum(
            1 for loc in range(1, 500)
            if generate_weather(loc, 150, "winter") == "sunny"
        )
        assert summer_sunny > winter_sunny


# ============================================================
# WorldEngine.tick_world
# ============================================================
class TestWorldEngineTick:
    def _make_world(self, db_session, *, day=1, season="winter", offset=0):
        w = World(
            name="测试世界",
            description="",
            season=season,
            day_of_year=day,
            year=1,
            season_offset=offset,
        )
        db_session.add(w)
        db_session.commit()
        db_session.refresh(w)
        return w

    def test_tick_advances_one_day(self, db_session):
        w = self._make_world(db_session, day=31, season="spring")
        engine = WorldEngine(session_factory=lambda: db_session)
        result = engine.tick_world(w.id, n=1)
        assert result["old_day"] == 31
        assert result["new_day"] == 32
        assert result["year_rollover"] is False

    def test_tick_advances_n_days(self, db_session):
        w = self._make_world(db_session, day=10, season="winter")
        engine = WorldEngine(session_factory=lambda: db_session)
        result = engine.tick_world(w.id, n=5)
        assert result["new_day"] == 15

    def test_tick_year_rollover(self, db_session):
        """day=365 推进到 day=1 + year+=1"""
        w = self._make_world(db_session, day=365, season="winter")
        engine = WorldEngine(session_factory=lambda: db_session)
        result = engine.tick_world(w.id, n=1)
        assert result["new_day"] == 1
        assert result["year_rollover"] is True
        # 刷新验证 year
        db_session.refresh(w)
        assert w.year == 2

    def test_tick_season_change_emits_event(self, db_session):
        """day=59 → 60：winter → spring，应触发立春事件"""
        w = self._make_world(db_session, day=59, season="winter")
        engine = WorldEngine(session_factory=lambda: db_session)
        result = engine.tick_world(w.id, n=1)
        assert result["season_changed"] is True
        assert result["new_season"] == "spring"
        assert len(result["events_created"]) == 1
        ev = result["events_created"][0]
        assert ev["title"] == "立春"
        assert ev["kind"] == "seasonal"

    def test_tick_no_season_change(self, db_session):
        """同季节区间内推进：不触发事件"""
        w = self._make_world(db_session, day=100, season="spring")
        engine = WorldEngine(session_factory=lambda: db_session)
        result = engine.tick_world(w.id, n=5)
        assert result["season_changed"] is False
        assert result["events_created"] == []

    def test_tick_world_not_found(self, db_session):
        engine = WorldEngine(session_factory=lambda: db_session)
        with pytest.raises(ValueError):
            engine.tick_world(9999, n=1)

    def test_tick_invalid_n(self, db_session):
        w = self._make_world(db_session)
        engine = WorldEngine(session_factory=lambda: db_session)
        with pytest.raises(ValueError):
            engine.tick_world(w.id, n=0)
        with pytest.raises(ValueError):
            engine.tick_world(w.id, n=-1)

    def test_tick_persists_to_db(self, db_session):
        """tick 完后数据库应反映新 day/season"""
        w = self._make_world(db_session, day=59, season="winter")
        engine = WorldEngine(session_factory=lambda: db_session)
        engine.tick_world(w.id, n=1)
        db_session.expire_all()  # 重新加载
        w2 = db_session.get(World, w.id)
        assert w2.day_of_year == 60
        assert w2.season == "spring"

    def test_tick_weather_changes_returned(self, db_session):
        """tick 完后应返回最多 5 个 location 的天气"""
        w = self._make_world(db_session, day=100, season="spring")
        # 创建几个 location
        for i in range(3):
            db_session.add(Location(
                world_id=w.id,
                name=f"地点{i}",
                kind="generic",
                climate="temperate",
            ))
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        result = engine.tick_world(w.id, n=1)
        assert len(result["weather_changes"]) == 3
        for wc in result["weather_changes"]:
            assert "weather" in wc
            assert wc["weather"] in WEATHERS


# ============================================================
# WorldEngine.get_context_for_character
# ============================================================
class TestGetContext:
    def test_returns_empty_for_nonexistent_character(self, db_session):
        engine = WorldEngine(session_factory=lambda: db_session)
        ctx = engine.get_context_for_character(9999)
        assert ctx == {}

    def test_returns_world_info(self, db_session, sample_character):
        """角色绑定 world → 应能读到 world 上下文"""
        w = World(name="ctx-info", season="spring", day_of_year=100, year=1)
        db_session.add(w)
        db_session.commit()
        db_session.refresh(w)
        sample_character.world_id = w.id
        sample_character.current_location_id = None
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        ctx = engine.get_context_for_character(sample_character.id)
        assert "world" in ctx
        assert ctx["world"]["id"] == w.id
        assert ctx["world"]["name"] == "ctx-info"

    def test_returns_location_with_path(self, db_session, sample_character):
        """角色有 current_location_id → 返回 location + path"""
        w = World(name="ctx-world", season="spring", day_of_year=100, year=1)
        db_session.add(w)
        db_session.flush()
        loc = Location(world_id=w.id, name="酒馆", kind="building")
        db_session.add(loc)
        db_session.flush()
        sample_character.world_id = w.id
        sample_character.current_location_id = loc.id
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        ctx = engine.get_context_for_character(sample_character.id)
        assert ctx.get("location") is not None
        assert ctx["location"]["name"] == "酒馆"
        assert "酒馆" in ctx["location"]["path"]
        assert ctx.get("weather") is not None
        assert ctx["weather"] in WEATHERS

    def test_location_from_other_world_excluded(self, db_session, sample_character):
        """loc.world_id != char.world_id → location 应被排除"""
        w1 = World(name="w1", season="spring", day_of_year=1, year=1)
        w2 = World(name="w2", season="spring", day_of_year=1, year=1)
        db_session.add(w1)
        db_session.add(w2)
        db_session.commit()
        db_session.refresh(w1)
        db_session.refresh(w2)
        loc_w2 = Location(world_id=w2.id, name="w2-loc")
        db_session.add(loc_w2)
        db_session.commit()
        db_session.refresh(loc_w2)
        sample_character.world_id = w1.id
        sample_character.current_location_id = loc_w2.id
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        ctx = engine.get_context_for_character(sample_character.id)
        # location 在不同 world，应被排除
        assert ctx.get("location") is None
        assert ctx.get("weather") is None

    def test_includes_recent_events(self, db_session, sample_character):
        """最近事件应被包含"""
        w = World(name="ev-world", season="spring", day_of_year=60, year=1)
        db_session.add(w)
        db_session.flush()
        ev = WorldEvent(
            world_id=w.id,
            title="立春",
            kind="seasonal",
            scope="public",
            day=60,
            year=1,
        )
        db_session.add(ev)
        sample_character.world_id = w.id
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        ctx = engine.get_context_for_character(sample_character.id)
        assert "recent_events" in ctx
        titles = [e["title"] for e in ctx["recent_events"]]
        assert "立春" in titles


# ============================================================
# WorldEngine.get_weather_for_location
# ============================================================
class TestGetWeather:
    def test_returns_weather(self, db_session):
        w = World(name="weather-w", season="spring", day_of_year=100, year=1)
        db_session.add(w)
        db_session.flush()
        loc = Location(world_id=w.id, name="loc")
        db_session.add(loc)
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        weather = engine.get_weather_for_location(loc.id, day_of_year=100)
        assert weather in WEATHERS

    def test_returns_none_for_nonexistent_location(self, db_session):
        engine = WorldEngine(session_factory=lambda: db_session)
        assert engine.get_weather_for_location(9999) is None

    def test_deterministic_for_same_day(self, db_session):
        """同一天同地点 → 同一天气"""
        w = World(name="det-w", season="winter", day_of_year=1, year=1)
        db_session.add(w)
        db_session.flush()
        loc = Location(world_id=w.id, name="det-loc")
        db_session.add(loc)
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        w1 = engine.get_weather_for_location(loc.id, day_of_year=200)
        w2 = engine.get_weather_for_location(loc.id, day_of_year=200)
        assert w1 == w2


# ============================================================
# WorldEngine.list_worlds
# ============================================================
class TestListWorlds:
    def test_list_empty(self, db_session):
        engine = WorldEngine(session_factory=lambda: db_session)
        worlds = engine.list_worlds()
        assert isinstance(worlds, list)

    def test_list_returns_all(self, db_session):
        for i in range(3):
            db_session.add(World(name=f"w{i}", season="winter", day_of_year=1, year=1))
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        worlds = engine.list_worlds()
        assert len(worlds) >= 3


# ============================================================
# WorldEngine 单例 + session_factory 注入
# ============================================================
class TestWorldEngineSingleton:
    def test_get_world_engine_returns_singleton(self):
        reset_world_engine()
        e1 = get_world_engine()
        e2 = get_world_engine()
        assert e1 is e2

    def test_reset_world_engine(self):
        reset_world_engine()
        e1 = get_world_engine()
        reset_world_engine()
        e2 = get_world_engine()
        assert e1 is not e2
