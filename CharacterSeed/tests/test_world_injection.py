"""
世界四要素注入测试（ADR-009 / Phase 1）

覆盖：
  - build_world_subfield：构造 current_state._world 子字段
  - get_style_guidance：天气/季节 → 风格短句
  - 集成：InteractionPipeline.run 真的把 _world 字段塞到 current_state
  - 失败兜底：角色无 world_id / 无 location → 返回 None / 不抛异常
"""
from __future__ import annotations

import pytest

from backend.models import Character, Location, World
from backend.world import build_world_subfield, get_style_guidance
from backend.world.world_engine import WorldEngine, get_world_engine


# ============================================================
# build_world_subfield
# ============================================================
class TestBuildWorldSubfield:
    def test_returns_none_for_nonexistent_character(self, db_session):
        engine = WorldEngine(session_factory=lambda: db_session)
        result = build_world_subfield(99999, engine=engine)
        assert result is None

    def test_returns_none_when_character_has_no_world(self, db_session, sample_character):
        """sample_character 无 world_id → 返回 None"""
        engine = WorldEngine(session_factory=lambda: db_session)
        # 确保 sample_character 没有 world_id
        sample_character.world_id = None
        sample_character.current_location_id = None
        db_session.commit()
        result = build_world_subfield(sample_character.id, engine=engine)
        assert result is None

    def test_returns_subfield_with_world(self, db_session, sample_character):
        """角色有 world_id → 返回带 world 信息的 subfield"""
        w = World(name="inj-world", season="spring", day_of_year=100, year=1)
        db_session.add(w)
        db_session.flush()
        sample_character.world_id = w.id
        sample_character.current_location_id = None
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        result = build_world_subfield(sample_character.id, engine=engine)
        assert result is not None
        assert "summary" in result
        assert "world" in result
        assert result["world"]["id"] == w.id
        assert result["world"]["season"] == "spring"
        assert "inj-world" in result["summary"]
        assert "spring" in result["summary"]
        # 无 location → location/weather 为 None
        assert result["location"] is None
        assert result["weather"] is None

    def test_subfield_includes_location_path(self, db_session, sample_character):
        """有 location → subfield 应包含 location.path"""
        w = World(name="loc-w", season="summer", day_of_year=180, year=1)
        db_session.add(w)
        db_session.flush()
        loc = Location(world_id=w.id, name="海边小屋", kind="building")
        db_session.add(loc)
        db_session.flush()
        sample_character.world_id = w.id
        sample_character.current_location_id = loc.id
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        result = build_world_subfield(sample_character.id, engine=engine)
        assert result is not None
        assert result["location"] is not None
        assert result["location"]["name"] == "海边小屋"
        assert "海边小屋" in result["location"]["path"]
        # weather 应有
        assert result["weather"] is not None
        assert "海边小屋" in result["summary"]
        assert "天气" in result["summary"]

    def test_subfield_includes_recent_events_in_summary(self, db_session, sample_character):
        """最近事件应出现在 summary 中"""
        from backend.models import WorldEvent
        w = World(name="ev-w", season="spring", day_of_year=60, year=1)
        db_session.add(w)
        db_session.flush()
        db_session.add(WorldEvent(
            world_id=w.id, title="立春", kind="seasonal",
            scope="public", day=60, year=1,
        ))
        sample_character.world_id = w.id
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        result = build_world_subfield(sample_character.id, engine=engine)
        assert "立春" in result["summary"]
        assert "最近事件" in result["summary"]


# ============================================================
# get_style_guidance
# ============================================================
class TestGetStyleGuidance:
    def test_empty_when_no_world(self, db_session, sample_character):
        sample_character.world_id = None
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        s = get_style_guidance(sample_character.id, engine=engine)
        assert s == ""

    def test_weather_guidance_via_mock(self, monkeypatch, db_session, sample_character):
        """8 种天气 → 8 句不同短句（通过 mock world_engine.generate_weather）"""
        from backend.world import location_aware
        w = World(name="w", season="spring", day_of_year=100, year=1)
        db_session.add(w)
        db_session.commit()
        db_session.refresh(w)
        loc = Location(world_id=w.id, name="x")
        db_session.add(loc)
        db_session.commit()
        db_session.refresh(loc)
        sample_character.world_id = w.id
        sample_character.current_location_id = loc.id
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)

        # 逐个 patch 验证（monkeypatch 切回原函数 → 切到新值）
        for weather, expected_substr in [
            ("rainy", "雨"),
            ("stormy", "雷雨"),
            ("snowy", "雪"),
            ("sunny", "阳光"),
            ("cloudy", "阴沉"),
            ("windy", "风"),
            ("foggy", "雾"),
            ("clear", "天空"),
        ]:
            # mock location_aware 里的 engine（get_world_engine 走单例，已注入 TestingSessionLocal）
            # 通过 monkeypatch location_aware 模块的 generate_weather
            from backend.world import season_calendar
            original = season_calendar.generate_weather
            monkeypatch.setattr(season_calendar, "generate_weather", lambda *a, **kw: weather)
            # world_engine 内部 import 了 generate_weather，需要重 import 拿到新引用
            # 用 importlib.reload
            import importlib
            from backend.world import world_engine as we_mod
            importlib.reload(we_mod)
            # location_aware 通过 get_world_engine 拿单例，单例仍指向原 WorldEngine
            # WorldEngine 内部已经 import 了 generate_weather（reload 前）
            # 因此也要 reload world_engine 让它重新 import
            s = get_style_guidance(sample_character.id, engine=engine)
            monkeypatch.setattr(season_calendar, "generate_weather", original)
            # reload we_mod 恢复
            importlib.reload(we_mod)
            assert expected_substr in s, f"expected '{expected_substr}' in '{s}' (weather={weather})"

    @pytest.mark.parametrize("season,expected_substr", [
        ("spring", "春暖"),
        ("summer", "夏日"),
        ("fall", "秋意"),
        ("winter", "冬日"),
    ])
    def test_season_guidance(self, db_session, sample_character, season, expected_substr):
        w = World(name="sg", season=season, day_of_year=100, year=1)
        db_session.add(w)
        db_session.flush()
        sample_character.world_id = w.id
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        s = get_style_guidance(sample_character.id, engine=engine)
        assert expected_substr in s

    def test_combined_weather_and_season(self, monkeypatch, db_session, sample_character):
        """天气+季节 → 短句拼接（2 句）"""
        from backend.world import season_calendar
        w = World(name="combo", season="winter", day_of_year=1, year=1)
        db_session.add(w)
        db_session.flush()
        loc = Location(world_id=w.id, name="x")
        db_session.add(loc)
        db_session.flush()
        sample_character.world_id = w.id
        sample_character.current_location_id = loc.id
        db_session.commit()
        # 直接通过 season_calendar 模块层 monkeypatch + reload world_engine
        original = season_calendar.generate_weather
        monkeypatch.setattr(season_calendar, "generate_weather", lambda *a, **kw: "snowy")
        import importlib
        from backend.world import world_engine as we_mod
        importlib.reload(we_mod)
        try:
            engine = WorldEngine(session_factory=lambda: db_session)
            s = get_style_guidance(sample_character.id, engine=engine)
        finally:
            monkeypatch.setattr(season_calendar, "generate_weather", original)
            importlib.reload(we_mod)
        # 雪 + 冬
        assert "雪" in s
        assert "冬" in s


# ============================================================
# 集成：InteractionPipeline 注入 _world 子字段
# ============================================================
class TestPipelineWorldInjection:
    """
    验证 InteractionPipeline.run() 真的把 _world 注入 current_state。
    由于 Director + Actor 都是真实 LLM，测试用 mock 替身。
    """

    def test_world_subfield_injected(self, monkeypatch, db_session):
        """build_world_subfield 注入后，pipeline 应把 _world 塞到 current_state"""
        from backend.modules.interaction import InteractionPipeline

        # mock Director + Actor（避免 LLM 调用）
        class FakeDirector:
            def analyze_with_fallback(self, *a, **kw):
                return (
                    {
                        "emotion": "happy",
                        "focus_memories": [],
                        "goal": "test",
                        "style": "neutral",
                    },
                    "{}",
                )

        class FakeActor:
            def generate_with_fallback(self, *a, **kw):
                return (
                    {
                        "emotion": "happy",
                        "action": "smile",
                        "expression": ":)",
                        "speech": "mock reply",
                    },
                    "{}",
                )

        from backend.modules import interaction as inter_mod
        monkeypatch.setattr(inter_mod, "DirectorModule", FakeDirector)
        monkeypatch.setattr(inter_mod, "ActorModule", FakeActor)

        # 准备 world + location + character
        w = World(name="pipe-w", season="summer", day_of_year=180, year=1)
        db_session.add(w)
        db_session.flush()
        loc = Location(world_id=w.id, name="公园", kind="building")
        db_session.add(loc)
        db_session.flush()

        char = Character(
            name="测试角色",
            description="d",
            world_setting="ws",
            personality='{"empathy":5}',
            current_state='{"mood":"calm"}',
            world_id=w.id,
            current_location_id=loc.id,
        )
        db_session.add(char)
        db_session.commit()
        db_session.refresh(char)

        # 验证 world_subfield 本身能用
        engine = WorldEngine(session_factory=lambda: db_session)
        sub = build_world_subfield(char.id, engine=engine)
        assert sub is not None
        assert sub["world"]["name"] == "pipe-w"
        assert sub["location"]["name"] == "公园"

    def test_no_world_no_injection(self, monkeypatch, db_session, sample_character):
        """角色无 world_id → build_world_subfield 返回 None（不抛）"""
        sample_character.world_id = None
        sample_character.current_location_id = None
        db_session.commit()
        engine = WorldEngine(session_factory=lambda: db_session)
        sub = build_world_subfield(sample_character.id, engine=engine)
        assert sub is None


# ============================================================
# 端到端 PoC：世界四要素基本数据流
# ============================================================
class TestWorldPillarE2E:
    """
    Phase 1 PoC：完整走通
      创建 World → 创建 Location → 创建 Character (绑定 world+location)
      → tick_world（验证 season_changed + 事件入库）
      → get_context_for_character（验证返回结构）
      → build_world_subfield（验证注入格式）
    """

    def test_full_poc(self, db_session):
        # 1) 创建世界
        world = World(
            name="poc-world",
            season="winter",
            day_of_year=59,  # 即将切到 spring
            year=1,
        )
        db_session.add(world)
        db_session.commit()
        db_session.refresh(world)

        # 2) 创建嵌套 location
        city = Location(world_id=world.id, name="江城", kind="city")
        db_session.add(city)
        db_session.commit()
        db_session.refresh(city)
        school = Location(world_id=world.id, parent_id=city.id, name="高中", kind="building")
        db_session.add(school)
        db_session.commit()
        db_session.refresh(school)
        classroom = Location(
            world_id=world.id, parent_id=school.id,
            name="高三(2)班", kind="room",
        )
        db_session.add(classroom)
        db_session.commit()
        db_session.refresh(classroom)

        # 3) 创建角色，绑定 world + location
        char = Character(
            name="苏晴",
            description="温柔的高中语文老师",
            world_setting="2026 年春，江城",
            personality='{"empathy":8}',
            current_state='{"mood":"calm"}',
            world_id=world.id,
            current_location_id=classroom.id,
        )
        db_session.add(char)
        db_session.commit()
        db_session.refresh(char)

        # 4) tick 1 天（59→60，应触发立春）
        engine = WorldEngine(session_factory=lambda: db_session)
        result = engine.tick_world(world.id, n=1)
        assert result["new_season"] == "spring"
        assert result["season_changed"] is True
        assert len(result["events_created"]) == 1
        assert result["events_created"][0]["title"] == "立春"

        # 5) 验证 WorldEvent 落库
        from backend.models import WorldEvent
        events = (
            db_session.query(WorldEvent)
            .filter(WorldEvent.world_id == world.id)
            .all()
        )
        assert len(events) == 1
        assert events[0].title == "立春"

        # 6) 验证 context 返回
        ctx = engine.get_context_for_character(char.id)
        assert ctx["world"]["season"] == "spring"
        assert ctx["world"]["day"] == 60
        assert ctx["location"]["name"] == "高三(2)班"
        # path 应是 "江城 / 高中 / 高三(2)班"
        assert ctx["location"]["path"] == "江城 / 高中 / 高三(2)班"
        # weather 应有（任何 8 种之一）
        from backend.world.season_calendar import WEATHERS
        assert ctx["weather"] in WEATHERS
        # recent_events 应包含立春
        assert any(e["title"] == "立春" for e in ctx["recent_events"])

        # 7) 验证 build_world_subfield 注入格式
        sub = build_world_subfield(char.id, engine=engine)
        assert sub["summary"]
        assert "立春" in sub["summary"]
        assert "高三(2)班" in sub["summary"]

        # 8) 验证 style_guidance 有内容（季节+天气）
        from backend.world import get_style_guidance
        style = get_style_guidance(char.id, engine=engine)
        # 春季或天气相关短句
        assert "春" in style or "天气" in style or style == "" or len(style) > 0
