"""
Phase 4 — 关系网查询 + Director 注入 + 跨角色事件 broadcast 测试

覆盖：
  - get_relationships_of：单/双向、类型过滤、强度排序
  - detect_relationship_changes：阈值 + 窗口
  - build_relationship_subfield：summary 拼接 + 失败静默
  - broadcast_world_event：WorldEvent → 所有角色 Event 写入
  - tick_world 集成：season change 自动广播
  - world_router 新端点：graph / preview / broadcast-event
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ======================================================================
# 关系查询
# ======================================================================
class TestGetRelationshipsOf:
    def test_returns_all_related(self, db, sample_character, sample_character_2):
        """[P1] 查 sample_character 1 应返回它参与的所有关系"""
        from backend.models import Relationship
        from backend.world import get_relationships_of

        # 1<2<3 顺序约束：sample_character(1) 与 sample_character_2(?) 之间建一条
        # 实际 sample_character 和 sample_character_2 谁 id 小就用谁做 a
        cid1, cid2 = sample_character.id, sample_character_2.id
        a, b = sorted([cid1, cid2])
        rel = Relationship(world_id=1, char_a_id=a, char_b_id=b, type="friend", strength=80)
        db.add(rel)
        db.commit()

        # 1 查 1 → 1 条
        r1 = get_relationships_of(db, cid1)
        assert len(r1) == 1
        assert r1[0]["type"] == "friend"
        assert r1[0]["strength"] == 80
        # 另一方 = cid2
        assert r1[0]["other_character_id"] == cid2
        assert r1[0]["other_character_name"] == sample_character_2.name

    def test_sort_by_strength_desc(self, db, sample_character, sample_character_2):
        """[P1] 多条关系按 strength DESC 排序"""
        from backend.models import Character, Relationship
        from backend.world import get_relationships_of

        # 建 1 个新角色让 sample_character 有 2 条关系
        from backend.crud import character as character_crud
        c3 = character_crud.create_character(db, name="c3", description="x", world_setting="y")
        db.commit()

        cid1 = sample_character.id
        a1, b1 = sorted([cid1, c3.id])
        db.add(Relationship(world_id=1, char_a_id=a1, char_b_id=b1, type="rival", strength=20))
        a2, b2 = sorted([cid1, sample_character_2.id])
        db.add(Relationship(world_id=1, char_a_id=a2, char_b_id=b2, type="friend", strength=80))
        db.commit()

        rels = get_relationships_of(db, cid1)
        assert len(rels) == 2
        assert rels[0]["strength"] == 80  # friend 排前
        assert rels[1]["strength"] == 20

    def test_filter_by_type(self, db, sample_character, sample_character_2):
        """[P1] include_types 只返回指定类型"""
        from backend.models import Character, Relationship
        from backend.world import get_relationships_of

        c3 = __import__('backend.crud.character', fromlist=['create_character']).create_character(
            db, name="c3", description="x", world_setting="y",
        )
        db.commit()
        cid1 = sample_character.id

        # 一条 friend + 一条 rival
        a1, b1 = sorted([cid1, c3.id])
        db.add(Relationship(world_id=1, char_a_id=a1, char_b_id=b1, type="rival", strength=20))
        a2, b2 = sorted([cid1, sample_character_2.id])
        db.add(Relationship(world_id=1, char_a_id=a2, char_b_id=b2, type="friend", strength=80))
        db.commit()

        only_friend = get_relationships_of(db, cid1, include_types=["friend"])
        assert len(only_friend) == 1
        assert only_friend[0]["type"] == "friend"

    def test_no_relationships(self, db, sample_character):
        """[P1] 无关系时返空列表"""
        from backend.world import get_relationships_of
        assert get_relationships_of(db, sample_character.id) == []


# ======================================================================
# 关系演化检测
# ======================================================================
class TestDetectRelationshipChanges:
    def test_detects_decline(self, db, sample_character, sample_character_2):
        """[P1] 窗口内 delta < 阈值 → '关系变差了'"""
        from backend.models import Relationship
        from backend.world import detect_relationship_changes

        cid1, cid2 = sorted([sample_character.id, sample_character_2.id])
        history = [
            {"ts": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "delta": -15},
            {"ts": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(), "delta": -20},
        ]
        rel = Relationship(
            world_id=1, char_a_id=cid1, char_b_id=cid2,
            type="friend", strength=30,
            history_json=json.dumps(history),
        )
        db.add(rel)
        db.commit()

        changes = detect_relationship_changes(db, sample_character.id)
        assert len(changes) == 1
        assert changes[0]["delta"] == -35
        assert "变差了" in changes[0]["summary"]

    def test_ignores_small_changes(self, db, sample_character, sample_character_2):
        """[P1] 窗口内 |delta| < threshold → 不报"""
        from backend.models import Relationship
        from backend.world import detect_relationship_changes

        cid1, cid2 = sorted([sample_character.id, sample_character_2.id])
        history = [
            {"ts": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "delta": -5},
        ]
        rel = Relationship(
            world_id=1, char_a_id=cid1, char_b_id=cid2,
            type="acquaintance", strength=0,
            history_json=json.dumps(history),
        )
        db.add(rel)
        db.commit()

        changes = detect_relationship_changes(db, sample_character.id)
        assert len(changes) == 0

    def test_ignores_old_changes(self, db, sample_character, sample_character_2):
        """[P1] 窗口外的事件不计入"""
        from backend.models import Relationship
        from backend.world import detect_relationship_changes

        cid1, cid2 = sorted([sample_character.id, sample_character_2.id])
        history = [
            {"ts": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(), "delta": -50},
        ]
        rel = Relationship(
            world_id=1, char_a_id=cid1, char_b_id=cid2,
            type="friend", strength=0,
            history_json=json.dumps(history),
        )
        db.add(rel)
        db.commit()

        # 默认 window_days=7，30 天前的变化不在窗口内
        changes = detect_relationship_changes(db, sample_character.id)
        assert len(changes) == 0


# ======================================================================
# Director 注入
# ======================================================================
class TestBuildRelationshipSubfield:
    def test_includes_top_relationships(self, db, sample_character, sample_character_2):
        """[P1] 注入结果含 top_relationships"""
        from backend.models import Relationship
        from backend.world import build_relationship_subfield

        cid1, cid2 = sorted([sample_character.id, sample_character_2.id])
        rel = Relationship(world_id=1, char_a_id=cid1, char_b_id=cid2, type="friend", strength=85)
        db.add(rel)
        db.commit()

        sub = build_relationship_subfield(sample_character.id)
        assert sub is not None
        assert "friend" in sub["summary"]
        assert len(sub["top_relationships"]) == 1
        assert sub["top_relationships"][0]["strength"] == 85

    def test_includes_recent_changes_in_summary(self, db, sample_character, sample_character_2):
        """[P1] 关系变化进入 summary"""
        from backend.models import Relationship
        from backend.world import build_relationship_subfield

        cid1, cid2 = sorted([sample_character.id, sample_character_2.id])
        history = [
            {"ts": datetime.now(timezone.utc).isoformat(), "delta": -30},
        ]
        rel = Relationship(
            world_id=1, char_a_id=cid1, char_b_id=cid2,
            type="rival", strength=-30,
            history_json=json.dumps(history),
        )
        db.add(rel)
        db.commit()

        sub = build_relationship_subfield(sample_character.id)
        assert sub is not None
        assert "变差了" in sub["summary"]
        assert len(sub["recent_changes"]) == 1

    def test_returns_none_when_no_data(self, db, sample_character):
        """[P1] 没有任何关系时返 None（不污染 prompt）"""
        from backend.world import build_relationship_subfield
        # sample_character 是用 create_character 默认无关系
        sub = build_relationship_subfield(sample_character.id)
        assert sub is None


# ======================================================================
# 跨角色事件 broadcast
# ======================================================================
class TestBroadcastWorldEvent:
    def test_broadcasts_to_all_chars(self, db, sample_character, sample_character_2):
        """[P1] 一个 WorldEvent → 同 world 所有角色各加一条 Event"""
        from backend.crud.character import update_character
        from backend.models import WorldEvent
        from backend.world import broadcast_world_event

        # sample_character 默认无 world_id，显式绑到 world 1
        update_character(db, sample_character.id, world_id=1)
        update_character(db, sample_character_2.id, world_id=1)

        wev = WorldEvent(
            world_id=1, title="春节",
            description="新年到", kind="global", scope="public",
            day=1, year=1,
        )
        db.add(wev)
        db.commit()
        db.refresh(wev)

        char_events = broadcast_world_event(db, wev)
        assert len(char_events) == 2  # sample_character + sample_character_2

        # 每个角色 timeline 都能查到
        from backend.models import Event
        for ch in [sample_character, sample_character_2]:
            ev = db.query(Event).filter(Event.character_id == ch.id, Event.day_number == 1).first()
            assert ev is not None
            assert "春节" in ev.content
            assert ev.event_type == "scene_event"

    def test_no_char_no_broadcast(self, db):
        """[P1] 同 world 0 个角色 → 不创建 Event"""
        from backend.models import WorldEvent
        from backend.world import broadcast_world_event

        # 用 world_id=1 但确保 sample_character 没绑（v003 migration 之前的状态）
        # 当前 fixture 创建了 sample_character 但 update_character 没绑 world
        wev = WorldEvent(
            world_id=1, title="空世界", kind="global", scope="public",
            day=1, year=1,
        )
        db.add(wev)
        db.commit()
        db.refresh(wev)

        char_events = broadcast_world_event(db, wev)
        # sample_character fixture 默认无 world_id → 应该 0 个角色受影响
        assert len(char_events) == 0

    def test_custom_content_template(self, db, sample_character):
        """[P1] 自定义 template 替换 {title}/{description}"""
        from backend.crud.character import update_character
        from backend.models import WorldEvent
        from backend.world import broadcast_world_event

        update_character(db, sample_character.id, world_id=1)

        wev = WorldEvent(
            world_id=1, title="高考", description="第一天",
            kind="global", scope="public", day=1, year=1,
        )
        db.add(wev)
        db.commit()
        db.refresh(wev)

        char_events = broadcast_world_event(
            db, wev,
            event_type="character_initiative",
            content_template="{character_name} 参加 {title}：{description}",
        )
        assert len(char_events) >= 1
        ev = char_events[0]
        assert ev.event_type == "character_initiative"
        assert sample_character.name in ev.content
        assert "高考" in ev.content
        assert "第一天" in ev.content


# ======================================================================
# 集成：tick_world 季节切换 → 广播
# ======================================================================
class TestTickBroadcastIntegration:
    def test_season_change_broadcasts_to_chars(self, db, sample_character, sample_character_2):
        """[P1] tick_world 跨立春 → 给所有角色 timeline 加 scene_event"""
        from backend.world import get_world_engine, reset_world_engine
        from backend.models import World
        from backend.crud.character import update_character
        # [P0-fix] 必须从 backend.database 拿 conftest 注入的 TestingSessionLocal
        # 直连 `from tests.conftest import TestingSessionLocal` 会因 pytest 把 conftest
        # 加载为 `conftest`（非 `tests.conftest`）而落到一个独立的测试 DB，结果报
        # "no such table: worlds"。这里用 conftest 在 setup 阶段 monkey-patch 到
        # backend.database.TestingSessionLocal 的同一个实例。
        from backend.database import TestingSessionLocal

        # [P0-fix] broadcast_world_event 按 world_id 查角色；sample_character
        # 创建时 world_id=None → 需绑定到默认世界
        update_character(db, sample_character.id, world_id=1)
        update_character(db, sample_character_2.id, world_id=1)

        # 重置单例，让它用测试 session
        reset_world_engine()
        from backend.world.world_engine import WorldEngine
        engine = WorldEngine(session_factory=TestingSessionLocal)

        # 把世界推到 day 1（winter）
        with engine._db() as db2:
            w = db2.get(World, 1)
            w.day_of_year = 1
            w.season = "winter"
            db2.commit()

        # tick 60 天 → day 61（spring），跨立春（day 60）
        result = engine.tick_world(1, n=60)
        assert result["season_changed"] is True
        assert result["events_broadcast"] >= 2  # 2 个角色

        # 验证角色 timeline 有立春事件
        from backend.models import Event
        with engine._db() as db2:
            for cid in [sample_character.id, sample_character_2.id]:
                ev = db2.query(Event).filter(
                    Event.character_id == cid,
                ).order_by(Event.id.desc()).first()
                assert ev is not None, f"cid={cid} 没有立春事件"
                assert ev.event_type == "scene_event"
                assert ev.status == "completed"

    def test_no_season_change_no_broadcast(self, db, sample_character):
        """[P1] tick 1 天（无季节切换）→ 不广播"""
        from backend.world.world_engine import WorldEngine, reset_world_engine
        from backend.models import World, Event
        # [P0-fix] 见 test_season_change_broadcasts_to_chars：必须从 backend.database 拿
        from backend.database import TestingSessionLocal

        reset_world_engine()
        engine = WorldEngine(session_factory=TestingSessionLocal)
        with engine._db() as db2:
            w = db2.get(World, 1)
            w.day_of_year = 100
            w.season = "spring"
            db2.commit()

        # tick 1 天（仍 spring）
        result = engine.tick_world(1, n=1)
        assert result["season_changed"] is False
        # events_broadcast 仍为 0（初始化值）
        assert result["events_broadcast"] == 0


# ======================================================================
# 端点：relationship graph + preview + broadcast
# ======================================================================
class TestRelationshipGraphEndpoint:
    def test_graph_returns_nodes_and_edges(self, client, db, sample_character, sample_character_2):
        """[P1] /api/worlds/1/relationship-graph → nodes + edges"""
        from backend.models import Relationship
        from backend.crud.character import update_character
        # [P0-fix] 节点按 world_id 过滤；sample_character 创建时 world_id=None
        # → 需要先绑定到默认世界（id=1）才能在图里出现
        update_character(db, sample_character.id, world_id=1)
        update_character(db, sample_character_2.id, world_id=1)
        cid1, cid2 = sorted([sample_character.id, sample_character_2.id])
        db.add(Relationship(world_id=1, char_a_id=cid1, char_b_id=cid2, type="friend", strength=70))
        db.commit()

        r = client.get("/api/worlds/1/relationship-graph")
        assert r.status_code == 200
        data = r.json()
        assert data["world_id"] == 1
        assert data["stats"]["character_count"] == 2
        assert data["stats"]["relationship_count"] == 1
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert data["edges"][0]["type"] == "friend"

    def test_graph_404_for_missing_world(self, client):
        r = client.get("/api/worlds/9999/relationship-graph")
        assert r.status_code == 404

    def test_preview_includes_relationships(self, client, db, sample_character, sample_character_2):
        """[P1] /api/characters/{cid}/relationships/preview → top_relationships"""
        from backend.models import Relationship
        cid1, cid2 = sorted([sample_character.id, sample_character_2.id])
        db.add(Relationship(world_id=1, char_a_id=cid1, char_b_id=cid2, type="mentor", strength=90))
        db.commit()

        r = client.get(f"/api/characters/{sample_character.id}/relationships/preview")
        assert r.status_code == 200
        data = r.json()
        assert "top_relationships" in data
        assert len(data["top_relationships"]) == 1

    def test_broadcast_event_endpoint(self, client, db, sample_character, sample_character_2):
        """[P1] /api/worlds/1/broadcast-event → 创建 WorldEvent + 广播"""
        from backend.crud.character import update_character
        update_character(db, sample_character.id, world_id=1)
        update_character(db, sample_character_2.id, world_id=1)

        r = client.post(
            "/api/worlds/1/broadcast-event",
            json={"title": "赛博春节", "description": "霓虹灯下的鞭炮声", "kind": "global"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["world_event"]["title"] == "赛博春节"
        assert data["broadcast_count"] == 2  # 2 个角色

        # 角色 timeline 有此事件
        from backend.models import Event
        ev = db.query(Event).filter(Event.character_id == sample_character.id, Event.content.like("%赛博春节%")).first()
        assert ev is not None

    def test_broadcast_event_with_location(self, client, db, sample_character):
        """[P1] 指定 location_id 的 broadcast"""
        from backend.crud.character import update_character
        from backend.models import Location
        update_character(db, sample_character.id, world_id=1)

        loc = Location(world_id=1, name="霓虹街", kind="city", climate="temperate")
        db.add(loc)
        db.commit()

        r = client.post(
            "/api/worlds/1/broadcast-event",
            json={"title": "街灯坏了", "kind": "local", "location_id": loc.id},
        )
        assert r.status_code == 201
        assert r.json()["broadcast_count"] >= 1
