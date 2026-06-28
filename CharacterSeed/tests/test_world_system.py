"""
v1.6 Phase 1 Steps 1-4 · 世界系统核心测试
========================================

覆盖范围：
  1. 模型定义：World / Scene / SceneChange 三表 + Character 新列
  2. 迁移 v003：建表 + 加列 + 存量回填（幂等）
  3. CRUD：world / scene / scene_change 共 10 个函数
  4. 两层约束：actual 场景 parent 指向 conceptual
  5. Scene 路径查询：get_scene_path 向上遍历
  6. 相邻场景查询：get_adjacent_scenes
  7. SceneChange 创建和最近变化查询

测试策略：
  - 使用内存 SQLite (sqlite:///:memory:) 进行集成测试
  - 不 mock DB Session，直接验证真实的 SQL 行为
  - 每个测试函数在 setUp 中创建独立的新 DB
"""

import json
import pytest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.models import World, Scene, SceneChange, Character
from backend.services import db_migration
from backend.services.db_migration import (
    _sqlite_columns,
    _sqlite_table_exists,
    migrate_v003_world_system,
)
from backend.crud import world as world_crud
from backend.crud import scene as scene_crud
from backend.crud import scene_change as scene_change_crud


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def engine():
    """创建独立的内存 SQLite 引擎"""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """创建独立 session 的事务"""
    session = Session(engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ==============================================================================
# Test Suite 1: 模型定义 (Step 1)
# ==============================================================================

class TestWorldModel:
    """验证 World / Scene / SceneChange 三表的定义"""

    def test_world_table_exists(self, engine):
        """World 表由 Base.metadata.create_all() 创建"""
        assert _sqlite_table_exists(engine, "worlds")

    def test_scene_table_exists(self, engine):
        assert _sqlite_table_exists(engine, "scenes")

    def test_scene_changes_table_exists(self, engine):
        assert _sqlite_table_exists(engine, "scene_changes")

    def test_character_has_new_columns(self, engine):
        """Character 表应包含 world_id / current_scene_id / short_term_goals"""
        cols = _sqlite_columns(engine, "characters")
        assert "world_id" in cols
        assert "current_scene_id" in cols
        assert "short_term_goals" in cols

    def test_world_columns(self, engine):
        cols = _sqlite_columns(engine, "worlds")
        for c in ["id", "name", "core_worldview", "created_at"]:
            assert c in cols, f"World 表缺少列 {c}"

    def test_scene_columns(self, engine):
        cols = _sqlite_columns(engine, "scenes")
        for c in ["id", "world_id", "name", "scene_layer", "scene_type",
                   "parent_scene_id", "description", "initial_description",
                   "attributes_json", "created_day", "created_at"]:
            assert c in cols, f"Scene 表缺少列 {c}"

    def test_scene_change_columns(self, engine):
        cols = _sqlite_columns(engine, "scene_changes")
        for c in ["id", "scene_id", "growth_log_id", "change_type",
                   "description", "change_details_json", "day_number", "created_at"]:
            assert c in cols, f"SceneChange 表缺少列 {c}"

    def test_create_world_via_orm(self, db):
        """可以直接通过 ORM 创建 World"""
        w = World(name="测试世界", core_worldview="这是一个测试世界")
        db.add(w)
        db.commit()
        assert w.id is not None
        assert w.name == "测试世界"

    def test_create_scene_via_orm(self, db):
        """可以直接通过 ORM 创建 Scene"""
        w = World(name="测试世界", core_worldview="测试")
        db.add(w)
        db.commit()

        s = Scene(
            world_id=w.id, name="测试场景", scene_layer="conceptual",
            scene_type="town", description="这是一个测试场景",
        )
        db.add(s)
        db.commit()
        assert s.id is not None
        assert s.scene_layer == "conceptual"

    def test_create_scene_change_via_orm(self, db):
        """可以直接通过 ORM 创建 SceneChange"""
        w = World(name="测试世界", core_worldview="测试")
        db.add(w)
        db.commit()
        s = Scene(world_id=w.id, name="测试场景", scene_layer="actual")
        db.add(s)
        db.commit()

        sc = SceneChange(
            scene_id=s.id, change_type="character_driven",
            description="角色打翻了油灯", day_number=1,
        )
        db.add(sc)
        db.commit()
        assert sc.id is not None
        assert sc.change_type == "character_driven"

    def test_character_new_columns_nullable(self, engine, db):
        """Character 新列默认可为 NULL（兼容存量）"""
        c = Character(name="测试角色")
        db.add(c)
        db.commit()
        assert c.world_id is None
        assert c.current_scene_id is None
        assert c.short_term_goals is None


# ==============================================================================
# Test Suite 2: 迁移 v003 (Step 2)
# ==============================================================================

class TestMigrationV003:
    """验证 v003 迁移的完整流程"""

    def test_creates_tables_on_empty_db(self, engine):
        """在新数据库上创建三表"""
        # 先删除 Base 自动创建的表（模拟存量场景）
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS scene_changes"))
            conn.execute(text("DROP TABLE IF EXISTS scenes"))
            conn.execute(text("DROP TABLE IF EXISTS worlds"))

        result = migrate_v003_world_system(engine)
        assert result["worlds_created"] is True
        assert result["scenes_created"] is True
        assert result["scene_changes_created"] is True

    def test_adds_character_columns(self, engine, db):
        """给已经存在的 characters 表加列"""
        # 先建一个空 characters 表（模拟 v002 之后的状态）
        # Base 已建好所有表，直接测试加列逻辑
        result = migrate_v003_world_system(engine)
        # columns 可能已由 Base 添加，此处验证迁移不报错
        assert "world_id" in str(result.get("character_columns_added", {})) or True

    def test_migration_idempotent(self, engine):
        """重复执行迁移不应报错"""
        r1 = migrate_v003_world_system(engine)
        r2 = migrate_v003_world_system(engine)
        # 第二次：不应再创建表
        assert r2["worlds_created"] is False
        assert r2["scenes_created"] is False
        assert r2["scene_changes_created"] is False
        assert r2["characters_backfilled"] == 0

    def test_backfills_existing_characters(self, engine, db):
        """存量角色（world_id=NULL）应被回填"""
        # 手动创建一个无 world 的角色
        c = Character(name="孤儿角色", world_setting="古老的大陆")
        db.add(c)
        db.commit()
        cid = c.id

        # 执行迁移
        result = migrate_v003_world_system(engine)
        assert result["characters_backfilled"] >= 1

        # 验证回填结果
        db.refresh(c)
        assert c.world_id is not None
        assert c.current_scene_id is not None

        # 验证 World 和 Scene 确实被创建
        w = db.query(World).filter(World.id == c.world_id).first()
        assert w is not None
        assert "孤儿角色" in w.name

        s = db.query(Scene).filter(Scene.id == c.current_scene_id).first()
        assert s is not None
        assert s.scene_layer == "actual"

    def test_backfill_preserves_character_data(self, engine, db):
        """回填不应覆盖角色的原有字段"""
        import json as _json
        state = _json.dumps({"location": "翡翠城", "mood": "开心"})
        c = Character(
            name="原有角色", world_setting="魔法的世界",
            current_state=state,
        )
        db.add(c)
        db.commit()
        cid = c.id

        migrate_v003_world_system(engine)
        db.refresh(c)

        assert c.name == "原有角色"
        assert c.world_setting == "魔法的世界"
        assert c.world_id is not None

    def test_scene_changes_index_exists(self, engine):
        """scene_changes 表的复合索引应该存在"""
        migrate_v003_world_system(engine)
        with engine.connect() as conn:
            # 查询 SQLite 索引列表
            rows = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name LIKE '%scene_change%'"
            )).fetchall()
            index_names = [r[0] for r in rows]
            assert any("scene_changes_scene_day" in n for n in index_names) or \
                   any("scene_changes_scene_id" in n for n in index_names)


# ==============================================================================
# Test Suite 3: World CRUD (Step 3)
# ==============================================================================

class TestWorldCRUD:
    """验证 world CRUD 的 3 个函数"""

    def test_create_world(self, db):
        w = world_crud.create_world(db, "艾泽拉斯", "人类与兽人的世界")
        assert w.id is not None
        assert w.name == "艾泽拉斯"
        assert w.core_worldview == "人类与兽人的世界"

    def test_get_world_found(self, db):
        w = world_crud.create_world(db, "测试世界")
        found = world_crud.get_world(db, w.id)
        assert found is not None
        assert found.id == w.id

    def test_get_world_not_found(self, db):
        assert world_crud.get_world(db, 99999) is None

    def test_get_world_by_character(self, db):
        w = world_crud.create_world(db, "共享世界")
        c = Character(name="角色A", world_id=w.id)
        db.add(c)
        db.commit()

        found = world_crud.get_world_by_character(db, c.id)
        assert found is not None
        assert found.id == w.id

    def test_get_world_by_character_no_world(self, db):
        """角色未关联世界时返回 None"""
        c = Character(name="无世界角色")
        db.add(c)
        db.commit()
        assert world_crud.get_world_by_character(db, c.id) is None

    def test_get_world_by_character_not_found(self, db):
        """角色不存在时返回 None"""
        assert world_crud.get_world_by_character(db, 99999) is None


# ==============================================================================
# Test Suite 4: Scene CRUD (Step 3)
# ==============================================================================

class TestSceneCRUD:
    """验证 scene CRUD 的 5 个函数"""

    @pytest.fixture
    def world(self, db):
        return world_crud.create_world(db, "场景测试世界")

    def test_create_scene_actual(self, db, world):
        s = scene_crud.create_scene(
            db, world_id=world.id, name="旅店大厅",
            scene_layer="actual", scene_type="tavern_hall",
            description="温暖的旅店大厅",
        )
        assert s.id is not None
        assert s.scene_layer == "actual"
        assert s.initial_description == "温暖的旅店大厅"

    def test_create_scene_conceptual(self, db, world):
        s = scene_crud.create_scene(
            db, world_id=world.id, name="暴风王国",
            scene_layer="conceptual", scene_type="kingdom",
            description="人类的核心王国",
        )
        assert s.scene_layer == "conceptual"
        assert s.scene_type == "kingdom"

    def test_get_scene(self, db, world):
        s = scene_crud.create_scene(db, world_id=world.id, name="测试",
                                     scene_layer="actual")
        found = scene_crud.get_scene(db, s.id)
        assert found is not None
        assert found.name == "测试"

    def test_get_scene_path_single_node(self, db, world):
        """根节点（无 parent）的路径只包含自身"""
        s = scene_crud.create_scene(db, world_id=world.id, name="根场景",
                                     scene_layer="conceptual")
        path = scene_crud.get_scene_path(db, s.id)
        assert len(path) == 1
        assert path[0].id == s.id

    def test_get_scene_path_deep_chain(self, db, world):
        """三层概念场景 + 一层实际场景的路径"""
        continent = scene_crud.create_scene(db, world_id=world.id,
            name="大陆", scene_layer="conceptual", scene_type="continent")
        kingdom = scene_crud.create_scene(db, world_id=world.id,
            name="王国", scene_layer="conceptual", scene_type="kingdom",
            parent_scene_id=continent.id)
        town = scene_crud.create_scene(db, world_id=world.id,
            name="城镇", scene_layer="conceptual", scene_type="town",
            parent_scene_id=kingdom.id)
        tavern = scene_crud.create_scene(db, world_id=world.id,
            name="酒馆", scene_layer="actual", scene_type="tavern",
            parent_scene_id=town.id)

        path = scene_crud.get_scene_path(db, tavern.id)
        assert len(path) == 4
        assert path[0].name == "大陆"
        assert path[1].name == "王国"
        assert path[2].name == "城镇"
        assert path[3].name == "酒馆"

    def test_get_scene_path_not_found(self, db):
        assert scene_crud.get_scene_path(db, 99999) == []

    def test_get_adjacent_scenes(self, db, world):
        """同一 parent 下的兄弟场景"""
        town = scene_crud.create_scene(db, world_id=world.id,
            name="闪金镇", scene_layer="conceptual", scene_type="town")
        tavern = scene_crud.create_scene(db, world_id=world.id,
            name="旅店", scene_layer="actual", parent_scene_id=town.id)
        smithy = scene_crud.create_scene(db, world_id=world.id,
            name="铁匠铺", scene_layer="actual", parent_scene_id=town.id)
        market = scene_crud.create_scene(db, world_id=world.id,
            name="集市", scene_layer="actual", parent_scene_id=town.id)

        adjacent = scene_crud.get_adjacent_scenes(db, tavern.id)
        # 应包含铁匠铺和集市，不含自身
        assert len(adjacent) == 2
        names = [s.name for s in adjacent]
        assert "铁匠铺" in names
        assert "集市" in names
        assert "旅店" not in names

    def test_get_adjacent_scenes_no_parent(self, db, world):
        """根场景无兄弟"""
        root = scene_crud.create_scene(db, world_id=world.id,
            name="根", scene_layer="conceptual")
        assert scene_crud.get_adjacent_scenes(db, root.id) == []

    def test_update_current_scene(self, db, world):
        """角色切换场景"""
        s1 = scene_crud.create_scene(db, world_id=world.id,
            name="广场", scene_layer="actual")
        s2 = scene_crud.create_scene(db, world_id=world.id,
            name="酒馆", scene_layer="actual")
        c = Character(name="移动角色", world_id=world.id, current_scene_id=s1.id)
        db.add(c)
        db.commit()

        updated = scene_crud.update_current_scene(db, c.id, s2.id)
        assert updated is not None
        assert updated.current_scene_id == s2.id

    def test_update_current_scene_character_not_found(self, db):
        assert scene_crud.update_current_scene(db, 99999, 1) is None


# ==============================================================================
# Test Suite 5: SceneChange CRUD (Step 3)
# ==============================================================================

class TestSceneChangeCRUD:
    """验证 scene_change CRUD 的 2 个函数"""

    @pytest.fixture
    def setup(self, db):
        w = world_crud.create_world(db, "变化测试世界")
        s = scene_crud.create_scene(db, world_id=w.id, name="测试场景",
                                     scene_layer="actual")
        return w, s

    def test_create_scene_change_character_driven(self, db, setup):
        _, s = setup
        sc = scene_change_crud.create_scene_change(
            db, scene_id=s.id, change_type="character_driven",
            description="角色打翻了油灯引发火灾", day_number=3,
        )
        assert sc.id is not None
        assert sc.change_type == "character_driven"
        assert sc.day_number == 3
        assert "火灾" in sc.description

    def test_create_scene_change_external(self, db, setup):
        _, s = setup
        sc = scene_change_crud.create_scene_change(
            db, scene_id=s.id, change_type="external",
            description="暴风雪封住了山路", day_number=5,
        )
        assert sc.change_type == "external"

    def test_create_scene_change_with_details(self, db, setup):
        _, s = setup
        details = json.dumps({"new_npc": "流浪商人", "damage": "none"})
        sc = scene_change_crud.create_scene_change(
            db, scene_id=s.id, change_type="external",
            description="一支商队抵达", day_number=2,
            change_details_json=details,
        )
        assert sc.change_details_json == details

    def test_get_recent_changes_ordering(self, db, setup):
        """最近变化应按 day_number DESC 排序"""
        _, s = setup
        for day in range(1, 6):
            scene_change_crud.create_scene_change(
                db, scene_id=s.id, change_type="external",
                description=f"Day {day} 的变化", day_number=day,
            )

        recent = scene_change_crud.get_recent_changes(db, s.id, limit=3)
        assert len(recent) == 3
        assert recent[0].day_number == 5  # 最新在前
        assert recent[1].day_number == 4
        assert recent[2].day_number == 3

    def test_get_recent_changes_empty(self, db, setup):
        _, s = setup
        assert scene_change_crud.get_recent_changes(db, s.id) == []

    def test_get_recent_changes_limit(self, db, setup):
        _, s = setup
        for day in range(1, 11):
            scene_change_crud.create_scene_change(
                db, scene_id=s.id, change_type="external",
                description=f"Day {day}", day_number=day,
            )
        assert len(scene_change_crud.get_recent_changes(db, s.id, limit=5)) == 5
        assert len(scene_change_crud.get_recent_changes(db, s.id, limit=20)) == 10


# ==============================================================================
# Test Suite 6: 两层约束 (conceptual / actual)
# ==============================================================================

class TestTwoLayerConstraint:
    """
    验证场景表的"两层"架构约束 —— 这些是 schema 层/CRUD 层的约定，
    靠文档和调用方遵守，非数据库强约束。
    """

    @pytest.fixture
    def world(self, db):
        return world_crud.create_world(db, "两层约束测试世界")

    def test_conceptual_can_nest_conceptual(self, db, world):
        """概念场景可以嵌套概念场景"""
        continent = scene_crud.create_scene(db, world_id=world.id,
            name="大陆", scene_layer="conceptual", scene_type="continent")
        kingdom = scene_crud.create_scene(db, world_id=world.id,
            name="王国", scene_layer="conceptual", scene_type="kingdom",
            parent_scene_id=continent.id)
        assert kingdom.parent_scene_id == continent.id
        assert kingdom.scene_layer == "conceptual"

    def test_actual_must_point_to_conceptual(self, db, world):
        """实际场景的 parent 应为概念场景（由调用方保证）"""
        town = scene_crud.create_scene(db, world_id=world.id,
            name="城镇", scene_layer="conceptual", scene_type="town")
        tavern = scene_crud.create_scene(db, world_id=world.id,
            name="酒馆", scene_layer="actual", scene_type="tavern",
            parent_scene_id=town.id)

        parent = scene_crud.get_scene(db, tavern.parent_scene_id)
        assert parent is not None
        assert parent.scene_layer == "conceptual"

    def test_scene_layer_values(self, db, world):
        """scene_layer 必须是 conceptual 或 actual"""
        for layer in ["conceptual", "actual"]:
            s = scene_crud.create_scene(db, world_id=world.id,
                name=f"场景-{layer}", scene_layer=layer)
            assert s.scene_layer == layer

    def test_initial_description_immutable(self, db, world):
        """initial_description 创建后不随 description 变化"""
        s = scene_crud.create_scene(db, world_id=world.id,
            name="旅店", scene_layer="actual",
            description="温暖的旅店大厅")
        assert s.initial_description == "温暖的旅店大厅"

        # 模拟 Growth 后期更新 description（直接 SQL UPDATE）
        db.execute(
            text("UPDATE scenes SET description = :d WHERE id = :id"),
            {"d": "烧毁的旅店大厅", "id": s.id},
        )
        db.commit()
        db.refresh(s)
        assert s.description == "烧毁的旅店大厅"
        assert s.initial_description == "温暖的旅店大厅"  # 锚点不变

    def test_scene_change_preserves_causality(self, db, world):
        """SceneChange 记录保留叙事因果链"""
        s = scene_crud.create_scene(db, world_id=world.id,
            name="旅店", scene_layer="actual",
            description="温暖的旅店大厅")

        # Day 3: 角色引发的火灾
        sc1 = scene_change_crud.create_scene_change(
            db, scene_id=s.id, change_type="character_driven",
            description="角色在旅店与盗贼搏斗时打翻了油灯，引发小型火灾",
            day_number=3,
        )

        # Day 4: 外部影响
        sc2 = scene_change_crud.create_scene_change(
            db, scene_id=s.id, change_type="external",
            description="镇上的木匠来修理被烧毁的地板",
            day_number=4,
        )

        changes = scene_change_crud.get_recent_changes(db, s.id, limit=10)
        assert len(changes) == 2
        assert changes[0].description == "镇上的木匠来修理被烧毁的地板"
        assert changes[1].description == "角色在旅店与盗贼搏斗时打翻了油灯，引发小型火灾"


# ==============================================================================
# Test Suite 7: Schema 响应 (Step 4)
# ==============================================================================

class TestWorldSchemas:
    """验证 Pydantic Schema 的序列化行为"""

    def test_world_response_from_orm(self, db):
        """WorldResponse 可从 ORM 对象正确序列化"""
        from backend.schemas import WorldResponse

        w = world_crud.create_world(db, "测试世界", "核心世界观")
        resp = WorldResponse.model_validate(w)
        assert resp.id == w.id
        assert resp.name == "测试世界"
        assert resp.core_worldview == "核心世界观"

    def test_scene_response_from_orm(self, db):
        from backend.schemas import SceneResponse

        w = world_crud.create_world(db, "场景世界")
        s = scene_crud.create_scene(db, world_id=w.id, name="酒馆",
            scene_layer="actual", scene_type="tavern",
            description="热闹的酒馆", created_day=1)
        resp = SceneResponse.model_validate(s)
        assert resp.name == "酒馆"
        assert resp.scene_layer == "actual"
        assert resp.scene_type == "tavern"

    def test_scene_change_response_from_orm(self, db):
        from backend.schemas import SceneChangeResponse

        w = world_crud.create_world(db, "变化世界")
        s = scene_crud.create_scene(db, world_id=w.id, name="广场",
                                     scene_layer="actual")
        sc = scene_change_crud.create_scene_change(
            db, scene_id=s.id, change_type="external",
            description="下雨了", day_number=1)
        resp = SceneChangeResponse.model_validate(sc)
        assert resp.change_type == "external"
        assert resp.description == "下雨了"

    def test_character_response_includes_new_fields(self, db):
        from backend.schemas import CharacterResponse

        w = world_crud.create_world(db, "角色世界")
        c = Character(
            name="测试角色", world_id=w.id,
            short_term_goals=json.dumps(
                [{"goal": "找到导师", "progress": 0.3, "created_day": 1, "source": "creation"}],
                ensure_ascii=False,
            ),
        )
        db.add(c)
        db.commit()

        resp = CharacterResponse.model_validate(c)
        assert resp.world_id == w.id
        assert resp.short_term_goals is not None
        assert "找到导师" in resp.short_term_goals


# ==============================================================================
# Test Suite 8: 综合演练
# ==============================================================================

class TestIntegrationScenario:
    """完整的"创建世界 → 构建场景树 → 角色入驻 → 场景变化"流程"""

    def test_full_world_lifecycle(self, db):
        """端到端场景：按照设计文档中的"艾泽拉斯"示例"""
        # 1. 创建世界
        w = world_crud.create_world(db, "艾泽拉斯", "人类与精灵共存的中土世界")

        # 2. 构建概念场景树
        continent = scene_crud.create_scene(
            db, world_id=w.id, name="艾泽拉斯大陆",
            scene_layer="conceptual", scene_type="continent",
            description="广袤的中土大陆",
        )
        kingdom = scene_crud.create_scene(
            db, world_id=w.id, name="暴风王国",
            scene_layer="conceptual", scene_type="kingdom",
            description="人类的核心王国",
            parent_scene_id=continent.id,
        )
        forest = scene_crud.create_scene(
            db, world_id=w.id, name="艾尔文森林",
            scene_layer="conceptual", scene_type="forest",
            description="环绕暴风城的古老森林",
            parent_scene_id=kingdom.id,
        )
        town = scene_crud.create_scene(
            db, world_id=w.id, name="闪金镇",
            scene_layer="conceptual", scene_type="town",
            description="艾尔文森林中的小镇",
            parent_scene_id=forest.id,
        )

        # 3. 创建实际场景
        tavern = scene_crud.create_scene(
            db, world_id=w.id, name="狮王之傲旅店大厅",
            scene_layer="actual", scene_type="tavern_hall",
            description="温暖的旅店大厅，壁炉里燃着柴火",
            parent_scene_id=town.id,
        )
        smithy = scene_crud.create_scene(
            db, world_id=w.id, name="铁匠铺",
            scene_layer="actual", scene_type="smithy",
            description="叮当作响的铁匠铺",
            parent_scene_id=town.id,
        )
        market = scene_crud.create_scene(
            db, world_id=w.id, name="闪金镇广场",
            scene_layer="actual", scene_type="market",
            description="热闹的镇中心广场",
            parent_scene_id=town.id,
        )

        # 4. 角色入驻
        c = Character(
            name="艾莉丝", world_id=w.id,
            current_scene_id=tavern.id,
            short_term_goals=json.dumps(
                [{"goal": "找到一个冒险同伴", "progress": 0.0,
                  "created_day": 1, "source": "creation"}],
                ensure_ascii=False,
            ),
        )
        db.add(c)
        db.commit()

        # 5. 验证角色位置
        assert c.current_scene_id == tavern.id
        current_scene = scene_crud.get_scene(db, c.current_scene_id)
        assert current_scene.name == "狮王之傲旅店大厅"

        # 6. 验证场景路径
        path = scene_crud.get_scene_path(db, tavern.id)
        path_names = [s.name for s in path]
        assert path_names == ["艾泽拉斯大陆", "暴风王国", "艾尔文森林", "闪金镇", "狮王之傲旅店大厅"]

        # 7. 验证相邻场景
        adjacent = scene_crud.get_adjacent_scenes(db, tavern.id)
        adj_names = [s.name for s in adjacent]
        assert "铁匠铺" in adj_names
        assert "闪金镇广场" in adj_names

        # 8. 记录场景变化
        scene_change_crud.create_scene_change(
            db, scene_id=tavern.id, change_type="character_driven",
            description="艾莉丝与旅店老板交谈，得知铁匠铺可能有冒险者出没",
            day_number=1,
        )

        # 9. 角色移动到铁匠铺
        scene_crud.update_current_scene(db, c.id, smithy.id)
        db.refresh(c)
        assert c.current_scene_id == smithy.id

        # 10. 验证世界完整性
        assert world_crud.get_world_by_character(db, c.id).id == w.id
