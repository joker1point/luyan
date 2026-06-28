"""
world_router REST 集成测试（ADR-009 / Phase 2）

覆盖端点（25 个 routes）：
  - Worlds:        list / create / get / patch / delete
  - World time:    state / tick / events / weather
  - Locations:     list / create / get / patch / delete / weather
  - Items:         list / create / get / patch / delete
  - Relationships: list-by-char / create / patch / delete
  - Character:     world-context
"""
from __future__ import annotations

from backend.database import TestingSessionLocal as TSL  # noqa: E402
import pytest


# ======================================================================
# Worlds
# ======================================================================
class TestWorlds:
    def test_list_worlds_seeds_default(self, client):
        """v003 migration 种子默认世界 (id=1) → GET 应返回 ≥ 1"""
        r = client.get("/api/worlds")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) >= 1
        # 默认世界存在
        default = next((w for w in data if w["id"] == 1), None)
        assert default is not None
        assert default["name"] == "默认世界"
        assert default["season"] in ("spring", "summer", "fall", "winter")
        assert 1 <= default["day_of_year"] <= 365
        assert default["year"] >= 1

    def test_get_world(self, client):
        r = client.get("/api/worlds/1")
        assert r.status_code == 200
        assert r.json()["id"] == 1
        assert r.json()["name"] == "默认世界"

    def test_get_world_404(self, client):
        r = client.get("/api/worlds/9999")
        assert r.status_code == 404

    def test_create_world(self, client):
        r = client.post("/api/worlds", json={
            "name": "魔法大陆",
            "description": "高魔奇幻",
            "season_offset": 180,  # 南半球
        })
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "魔法大陆"
        assert body["season_offset"] == 180
        # 验证可读
        r2 = client.get(f"/api/worlds/{body['id']}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "魔法大陆"

    def test_patch_world(self, client):
        r = client.patch("/api/worlds/1", json={"name": "默认世界 v2"})
        assert r.status_code == 200
        assert r.json()["name"] == "默认世界 v2"
        # 还原
        client.patch("/api/worlds/1", json={"name": "默认世界"})

    def test_delete_world_with_chars_400(self, client, sample_character, db):
        sample_character.world_id = 1
        db.commit()
        r = client.delete("/api/worlds/1")
        assert r.status_code == 400
        assert "characters" in r.json()["detail"]

    def test_delete_world_empty_204(self, client):
        # 建一个空世界再删
        r = client.post("/api/worlds", json={"name": "临时世界"})
        wid = r.json()["id"]
        r2 = client.delete(f"/api/worlds/{wid}")
        assert r2.status_code == 204
        # 二次访问 404
        r3 = client.get(f"/api/worlds/{wid}")
        assert r3.status_code == 404


# ======================================================================
# World time
# ======================================================================
class TestWorldTime:
    def test_get_state(self, client):
        r = client.get("/api/worlds/1/state")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == 1
        assert body["season"] in ("spring", "summer", "fall", "winter")
        assert 1 <= body["day_of_year"] <= 365

    def test_tick_default_1_day(self, client):
        r1 = client.get("/api/worlds/1/state")
        old_day = r1.json()["day_of_year"]
        r2 = client.post("/api/worlds/1/tick")
        assert r2.status_code == 200
        body = r2.json()
        assert body["old_day"] == old_day
        assert body["new_day"] == old_day + 1 if old_day < 365 else 1

    def test_tick_n_days(self, client):
        r = client.post("/api/worlds/1/tick", json={"n": 30})
        assert r.status_code == 200
        assert r.json()["n"] == 30

    def test_tick_invalid_n_400(self, client):
        r = client.post("/api/worlds/1/tick", json={"n": 0})
        assert r.status_code == 422  # pydantic validation
        r2 = client.post("/api/worlds/1/tick", json={"n": -1})
        assert r2.status_code == 422

    def test_tick_world_not_found(self, client):
        r = client.post("/api/worlds/9999/tick", json={"n": 1})
        assert r.status_code == 404

    def test_list_events(self, client):
        r = client.get("/api/worlds/1/events")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        # tick 几次后应产生立春事件（如果 day 跨到 60）
        # 但默认 day=1 可能跨好几年才到 60，不强制

    def test_list_weather_empty(self, client):
        """默认世界无 location → 返回空列表"""
        r = client.get("/api/worlds/1/weather")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_weather_with_locations(self, client, db):
        from backend.models import Location
        with db as s:
            for i in range(3):
                s.add(Location(world_id=1, name=f"地{i}", kind="generic", climate="temperate"))
            s.commit()
        r = client.get("/api/worlds/1/weather")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 3
        for w in data:
            assert "weather" in w
            assert w["weather"] in ("sunny", "cloudy", "rainy", "stormy", "snowy", "windy", "foggy", "clear")


# ======================================================================
# Locations
# ======================================================================
class TestLocations:
    def test_list_empty(self, client):
        r = client.get("/api/worlds/1/locations")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_and_get(self, client):
        r = client.post("/api/worlds/1/locations", json={
            "name": "无名酒馆",
            "kind": "building",
            "climate": "temperate",
        })
        assert r.status_code == 201
        loc = r.json()
        assert loc["name"] == "无名酒馆"
        assert loc["kind"] == "building"
        assert loc["path"] == "无名酒馆"  # root
        # get
        r2 = client.get(f"/api/locations/{loc['id']}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "无名酒馆"

    def test_create_with_invalid_parent_400(self, client):
        r = client.post("/api/worlds/1/locations", json={
            "name": "X",
            "parent_id": 9999,
        })
        assert r.status_code == 400

    def test_create_nested_path(self, client):
        r1 = client.post("/api/worlds/1/locations", json={"name": "东京", "kind": "city"})
        tokyo_id = r1.json()["id"]
        r2 = client.post("/api/worlds/1/locations", json={"name": "涩谷", "parent_id": tokyo_id, "kind": "building"})
        assert r2.status_code == 201
        cafe = r2.json()
        # path 应该是 "东京 / 涩谷"
        assert cafe["path"] == "东京 / 涩谷"

    def test_patch_location(self, client):
        r = client.post("/api/worlds/1/locations", json={"name": "old"})
        lid = r.json()["id"]
        r2 = client.patch(f"/api/locations/{lid}", json={"name": "new", "kind": "dungeon"})
        assert r2.status_code == 200
        assert r2.json()["name"] == "new"
        assert r2.json()["kind"] == "dungeon"

    def test_patch_self_parent_400(self, client):
        r = client.post("/api/worlds/1/locations", json={"name": "X"})
        lid = r.json()["id"]
        r2 = client.patch(f"/api/locations/{lid}", json={"parent_id": lid})
        assert r2.status_code == 400

    def test_delete_location(self, client, sample_character):
        r = client.post("/api/worlds/1/locations", json={"name": "TBD"})
        lid = r.json()["id"]
        sample_character.current_location_id = lid
        from backend.database import TestingSessionLocal
        with TestingSessionLocal() as db:
            db.commit()
        r2 = client.delete(f"/api/locations/{lid}")
        assert r2.status_code == 204
        # 角色的 current_location_id 应被置 NULL（删除副作用）
        with TestingSessionLocal() as db:
            char = db.get(type(sample_character).__class__ if False else __import__("backend.models", fromlist=["Character"]).Character, sample_character.id)
            assert char.current_location_id is None

    def test_location_weather(self, client):
        r = client.post("/api/worlds/1/locations", json={"name": "酒馆", "climate": "temperate"})
        lid = r.json()["id"]
        r2 = client.get(f"/api/locations/{lid}/weather")
        assert r2.status_code == 200
        body = r2.json()
        assert body["location_id"] == lid
        assert body["weather"] in ("sunny", "cloudy", "rainy", "stormy", "snowy", "windy", "foggy", "clear")
        # day=1 默认是 winter
        assert body["season"] == "winter"

    def test_location_weather_specific_day(self, client):
        r = client.post("/api/worlds/1/locations", json={"name": "X"})
        lid = r.json()["id"]
        r2 = client.get(f"/api/locations/{lid}/weather?day=100")
        assert r2.status_code == 200
        assert r2.json()["day"] == 100
        assert r2.json()["season"] == "spring"


# ======================================================================
# Items
# ======================================================================
class TestItems:
    def test_list_empty(self, client):
        r = client.get("/api/worlds/1/items")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_with_character_owner(self, client, sample_character):
        r = client.post("/api/worlds/1/items", json={
            "name": "红宝石戒指",
            "owner_kind": "character",
            "owner_id": sample_character.id,
            "rarity": "rare",
            "value": 100,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "红宝石戒指"
        assert body["owner_kind"] == "character"
        assert body["rarity"] == "rare"

    def test_create_with_location_owner(self, client):
        r1 = client.post("/api/worlds/1/locations", json={"name": "宝箱点"})
        loc_id = r1.json()["id"]
        r2 = client.post("/api/worlds/1/items", json={
            "name": "破旧木箱",
            "owner_kind": "location",
            "owner_id": loc_id,
        })
        assert r2.status_code == 201

    def test_create_invalid_owner_400(self, client):
        r = client.post("/api/worlds/1/items", json={
            "name": "X",
            "owner_kind": "character",
            "owner_id": 9999,
        })
        assert r.status_code == 400

    def test_filter_by_owner(self, client, sample_character, sample_character_2):
        client.post("/api/worlds/1/items", json={
            "name": "A", "owner_kind": "character", "owner_id": sample_character.id,
        })
        client.post("/api/worlds/1/items", json={
            "name": "B", "owner_kind": "character", "owner_id": sample_character_2.id,
        })
        r = client.get(f"/api/worlds/1/items?owner_kind=character&owner_id={sample_character.id}")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "A"

    def test_get_patch_delete_item(self, client, sample_character):
        r = client.post("/api/worlds/1/items", json={
            "name": "old", "owner_kind": "character", "owner_id": sample_character.id,
        })
        iid = r.json()["id"]
        r2 = client.get(f"/api/items/{iid}")
        assert r2.status_code == 200
        r3 = client.patch(f"/api/items/{iid}", json={"name": "new", "value": 999})
        assert r3.status_code == 200
        assert r3.json()["name"] == "new"
        assert r3.json()["value"] == 999
        r4 = client.delete(f"/api/items/{iid}")
        assert r4.status_code == 204


# ======================================================================
# Relationships
# ======================================================================
class TestRelationships:
    def test_list_empty(self, client, sample_character):
        r = client.get(f"/api/characters/{sample_character.id}/relationships")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_normalizes_pair(self, client, sample_character, sample_character_2, db):
        # 给 sample_character_2 绑默认世界
        sample_character_2.world_id = 1
        db.commit()
        # 注意 sample_character.id < sample_character_2.id，传反了顺序也应被规范化
        r = client.post("/api/worlds/1/relationships", json={
            "char_a_id": sample_character_2.id,
            "char_b_id": sample_character.id,
            "type": "friend",
            "strength": 50,
        })
        assert r.status_code == 201
        body = r.json()
        # char_a_id 应该是较小者
        assert body["char_a_id"] == sample_character.id
        assert body["char_b_id"] == sample_character_2.id

    def test_create_self_relationship_400(self, client, sample_character):
        r = client.post("/api/worlds/1/relationships", json={
            "char_a_id": sample_character.id,
            "char_b_id": sample_character.id,
            "type": "friend",
        })
        assert r.status_code == 400

    def test_create_duplicate_409(self, client, sample_character, sample_character_2, db):
        sample_character_2.world_id = 1
        db.commit()
        r1 = client.post("/api/worlds/1/relationships", json={
            "char_a_id": sample_character.id,
            "char_b_id": sample_character_2.id,
            "type": "friend",
        })
        assert r1.status_code == 201
        r2 = client.post("/api/worlds/1/relationships", json={
            "char_a_id": sample_character.id,
            "char_b_id": sample_character_2.id,
            "type": "rival",
        })
        assert r2.status_code == 409

    def test_patch_and_delete_relationship(self, client, sample_character, sample_character_2, db):
        sample_character_2.world_id = 1
        db.commit()
        r = client.post("/api/worlds/1/relationships", json={
            "char_a_id": sample_character.id,
            "char_b_id": sample_character_2.id,
            "type": "friend",
            "strength": 0,
        })
        rid = r.json()["id"]
        r2 = client.patch(f"/api/relationships/{rid}", json={"type": "lover", "strength": 80})
        assert r2.status_code == 200
        assert r2.json()["type"] == "lover"
        assert r2.json()["strength"] == 80
        # last_interaction_at 应被更新
        assert r2.json()["last_interaction_at"] is not None
        # delete
        r3 = client.delete(f"/api/relationships/{rid}")
        assert r3.status_code == 204

    def test_list_relationships_from_both_sides(self, client, sample_character, sample_character_2, db):
        """同一关系在两个角色的 list endpoint 都应出现"""
        sample_character_2.world_id = 1
        db.commit()
        client.post("/api/worlds/1/relationships", json={
            "char_a_id": sample_character.id,
            "char_b_id": sample_character_2.id,
            "type": "rival",
        })
        r1 = client.get(f"/api/characters/{sample_character.id}/relationships")
        r2 = client.get(f"/api/characters/{sample_character_2.id}/relationships")
        assert len(r1.json()) == 1
        assert len(r2.json()) == 1
        assert r1.json()[0]["id"] == r2.json()[0]["id"]


# ======================================================================
# Character world context
# ======================================================================
class TestCharacterWorldContext:
    def test_returns_context(self, client, sample_character, db):
        # 默认 fixture 不绑世界；显式绑
        sample_character.world_id = 1
        db.commit()
        r = client.get(f"/api/characters/{sample_character.id}/world-context")
        assert r.status_code == 200
        body = r.json()
        assert "world" in body
        assert body["world"]["id"] == 1
        assert body["world"]["name"] == "默认世界"

    def test_returns_empty_for_char_without_world(self, client, db):
        from backend.crud.character import create_character
        with db as s:
            ch = create_character(
                db=s, name="孤狼", description="无世界",
                world_setting="",
                personality={}, current_state={},
            )
            ch.world_id = None
            s.commit()
            char_id = ch.id
        r = client.get(f"/api/characters/{char_id}/world-context")
        assert r.status_code == 200
        # 没有 world 应返回空 dict
        assert r.json() == {}

    def test_404_for_nonexistent_character(self, client):
        r = client.get("/api/characters/9999/world-context")
        assert r.status_code == 404
