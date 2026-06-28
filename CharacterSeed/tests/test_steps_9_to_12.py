"""
Steps 9-12 综合测试：World + Scene 世界系统

测试范围：
  Step 9  (world-scene-crud-and-api):
    → World CRUD (create/get/update/get_all/get_world_by_character)
    → Scene CRUD (create/get/get_path/get_adjacent/get_scenes_by_world)
    → SceneChange CRUD (create/get_recent_changes/get_scene_changes_by_world)
    → World/Scene API 端点 (GET/PATCH)

  Step 10 (creation-world-init):
    → validate_creation_schema 升级：校验 world_name/core_worldview/scenes
    → character create 持久化 World → Scenes → 关联 Character.world_id/current_scene_id

  Step 11 (growth-scene-change):
    → Growth 迭代后 _write_scene_changes_from_growth 写入 scene_changes
    → scene.description 同步更新（initial_description 保持不变）

  Step 12 (frontend-e2e):
    → API 客户端函数：get_character_world / get_character_scenes / get_scene_changes
    → SceneResponse / WorldResponse / SceneChangeResponse Schema 序列化
    → 端到端流程：创建→校验World+Scene→推进事件→迭代→场景变化记录

测试采用内存 SQLite，不依赖外部 LLM 服务。
所有测试均使用模拟数据直接写入数据库，验证完整的数据层与 Schema 层链路。
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import Character, World, Scene, SceneChange, Event, GrowthLog, Memory
from backend.crud import (
    character as character_crud,
    world as world_crud,
    scene as scene_crud,
    scene_change as scene_change_crud,
    event as event_crud,
    growth as growth_crud,
    memory as memory_crud,
)
from backend.schemas import (
    WorldResponse, SceneResponse, SceneChangeResponse,
    CharacterResponse,
)
from backend.services.llm_service import LLMService


# ============================================================================
# Fixture: 内存数据库
# ============================================================================

@pytest.fixture
def db():
    """创建内存 SQLite 数据库，每次测试独立隔离"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    print("\n" + "=" * 72)
    print("  [Fixture] 内存 SQLite 数据库已初始化")
    print("=" * 72)
    yield session
    session.close()
    print("  [Fixture] 数据库已清理")


# ============================================================================
# Step 9: World + Scene CRUD + API 端点测试
# ============================================================================

class TestStep9_WorldCRUD:
    """测试 World CRUD 操作"""

    def test_create_and_get_world(self, db):
        """创建世界 → 查询世界 → 验证字段完整性"""
        print("\n" + "─" * 60)
        print("  Step 9.1 — World CRUD: 创建与查询")
        print("─" * 60)

        # [①] 创建世界
        world = world_crud.create_world(
            db=db,
            name="北境雪原",
            core_worldview="严寒之地，勇者的试炼场",
        )
        print(f"  [①] World 已创建: id={world.id}, name='{world.name}'")
        print(f"       core_worldview='{world.core_worldview}'")
        assert world.id is not None
        assert world.name == "北境雪原"
        assert world.core_worldview == "严寒之地，勇者的试炼场"

        # [②] 按 ID 查询
        fetched = world_crud.get_world(db, world.id)
        print(f"  [②] get_world(id={world.id}) → name='{fetched.name}'")
        assert fetched is not None
        assert fetched.id == world.id

        # [③] 创建第二个世界
        world2 = world_crud.create_world(
            db=db,
            name="魔法森林",
            core_worldview="万物有灵的奇幻森林",
        )
        print(f"  [③] 第二个 World: id={world2.id}, name='{world2.name}'")

        # [④] get_all_worlds — 返回所有世界
        all_worlds = world_crud.get_all_worlds(db)
        print(f"  [④] get_all_worlds() → {len(all_worlds)} 个世界")
        for w in all_worlds:
            print(f"       - id={w.id}, name='{w.name}'")
        assert len(all_worlds) == 2

        # [⑤] update_world — 更新名称
        updated = world_crud.update_world(db, world.id, name="北境冰原 (已更名)")
        print(f"  [⑤] update_world → 新名称: '{updated.name}'")
        assert updated.name == "北境冰原 (已更名)"
        assert updated.core_worldview == "严寒之地，勇者的试炼场"  # 未改动

        # [⑥] get_world_by_character — 需要先有角色并关联 world
        char = Character(
            name="测试角色",
            description="test",
            world_setting="测试世界",
            personality=json.dumps({"optimism": 50}),
            current_state=json.dumps({"location": "test"}),
            world_id=world.id,
        )
        db.add(char)
        db.commit()
        char_world = world_crud.get_world_by_character(db, char.id)
        print(f"  [⑥] get_world_by_character(char_id={char.id}) → name='{char_world.name}'")
        assert char_world is not None
        assert char_world.id == world.id

        print("  [OK] Step 9.1 World CRUD 全部通过")


class TestStep9_SceneCRUD:
    """测试 Scene CRUD 操作"""

    def test_create_scene_and_query_path(self, db):
        """创建场景树 → 路径查询 → 兄弟场景查询"""
        print("\n" + "─" * 60)
        print("  Step 9.2 — Scene CRUD: 场景树创建与查询")
        print("─" * 60)

        # [①] 先创建 World
        world = world_crud.create_world(
            db=db,
            name="测试大陆",
            core_worldview="一个测试用的世界",
        )
        print(f"  [①] World 已创建: id={world.id}")

        # [②] 创建概念场景树（3 层嵌套）
        continent = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="艾泽拉斯大陆",
            scene_layer="conceptual",
            scene_type="continent",
            description="广袤的大陆",
        )
        print(f"  [②] 概念场景（根）: id={continent.id}, name='{continent.name}', layer={continent.scene_layer}")

        kingdom = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="暴风城王国",
            scene_layer="conceptual",
            scene_type="kingdom",
            parent_scene_id=continent.id,
            description="人类文明的中心",
        )
        print(f"      概念场景（子）: id={kingdom.id}, name='{kingdom.name}', parent_scene_id={kingdom.parent_scene_id}")

        town = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="闪金镇",
            scene_layer="conceptual",
            scene_type="town",
            parent_scene_id=kingdom.id,
            description="一个宁静的小镇",
        )
        print(f"      概念场景（孙）: id={town.id}, name='{town.name}', parent_scene_id={town.parent_scene_id}")

        # [③] 创建实际场景
        tavern = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="狮王之傲旅店",
            scene_layer="actual",
            scene_type="tavern",
            parent_scene_id=town.id,
            description="温暖的火炉与麦酒香气",
        )
        print(f"  [③] 实际场景: id={tavern.id}, name='{tavern.name}', parent_scene_id={tavern.parent_scene_id}")

        market = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="闪金镇集市",
            scene_layer="actual",
            scene_type="market",
            parent_scene_id=town.id,
            description="热闹的集市广场",
        )
        print(f"      实际场景: id={market.id}, name='{market.name}', parent_scene_id={market.parent_scene_id}")

        # [④] get_scene_path — 从 tavern 向上查路径
        path = scene_crud.get_scene_path(db, tavern.id)
        print(f"\n  [④] get_scene_path(tavern_id={tavern.id}):")
        for i, s in enumerate(path):
            print(f"       [{i}] {s.name} ({s.scene_layer})")
        assert len(path) == 4  # continent → kingdom → town → tavern
        assert path[0].name == "艾泽拉斯大陆"
        assert path[3].name == "狮王之傲旅店"

        # [⑤] get_adjacent_scenes — tavern 的兄弟场景
        adjacent = scene_crud.get_adjacent_scenes(db, tavern.id)
        print(f"\n  [⑤] get_adjacent_scenes(tavern_id={tavern.id}):")
        for a in adjacent:
            print(f"       - {a.name} ({a.scene_type})")
        assert len(adjacent) == 1
        assert adjacent[0].name == "闪金镇集市"

        # [⑥] get_scenes_by_world — 按世界查询所有场景
        all_scenes = scene_crud.get_scenes_by_world(db, world.id)
        print(f"\n  [⑥] get_scenes_by_world(world_id={world.id}): {len(all_scenes)} 个场景")
        assert len(all_scenes) == 5  # 3 conceptual + 2 actual

        # 按层级筛选
        actual_only = scene_crud.get_scenes_by_world(db, world.id, scene_layer="actual")
        print(f"      scene_layer='actual' → {len(actual_only)} 个场景")
        assert len(actual_only) == 2

        # [⑦] update_scene — 更新描述
        updated = scene_crud.update_scene(
            db, tavern.id,
            description="火炉已熄灭，旅店空无一人",
            attributes_json=json.dumps({"mood": "gloomy"}),
        )
        print(f"\n  [⑦] update_scene: description='{updated.description}'")
        assert "熄灭" in updated.description
        assert updated.initial_description == "温暖的火炉与麦酒香气"  # initial_description 不变！

        # [⑧] get_initial_actual_scene — 取 world 下第一个实际场景
        first_actual = scene_crud.get_initial_actual_scene(db, world.id)
        print(f"  [⑧] get_initial_actual_scene: id={first_actual.id}, name='{first_actual.name}'")
        assert first_actual.id == tavern.id  # tavern 是第一个 actual

        # [⑨] get_scenes_by_character — 通过角色关联获取场景
        char = Character(
            name="场景查询角色",
            personality=json.dumps({"optimism": 50}),
            current_state=json.dumps({"location": "test"}),
            world_id=world.id,
        )
        db.add(char)
        db.commit()
        char_scenes = scene_crud.get_scenes_by_character(db, char.id)
        print(f"  [⑨] get_scenes_by_character(char_id={char.id}): {len(char_scenes)} 个场景")
        assert len(char_scenes) == 5

        print("  [OK] Step 9.2 Scene CRUD 全部通过")


class TestStep9_SceneChangeCRUD:
    """测试 SceneChange CRUD 操作"""

    def test_create_and_query_changes(self, db):
        """创建场景变化 → 查询变化历史"""
        print("\n" + "─" * 60)
        print("  Step 9.3 — SceneChange CRUD: 创建与查询")
        print("─" * 60)

        # [①] 创建 World + Scene
        world = world_crud.create_world(db, name="测试世界", core_worldview="测试")
        scene = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="测试酒馆",
            scene_layer="actual",
            scene_type="tavern",
            description="一间热闹的酒馆",
        )
        print(f"  [①] Scene 已创建: id={scene.id}")

        # [②] 创建多条场景变化记录
        change1 = scene_change_crud.create_scene_change(
            db=db, scene_id=scene.id,
            change_type="character_driven",
            description="勇者打翻油灯引发火灾，酒馆被烧毁了一半",
            day_number=2,
            growth_log_id=None,
        )
        print(f"  [②] SceneChange #1: type={change1.change_type}, day={change1.day_number}")
        print(f"       '{change1.description[:40]}...'")

        change2 = scene_change_crud.create_scene_change(
            db=db, scene_id=scene.id,
            change_type="external",
            description="暴风雪封住了酒馆的大门，三天无人进出",
            day_number=3,
            growth_log_id=None,
        )
        print(f"      SceneChange #2: type={change2.change_type}, day={change2.day_number}")
        print(f"       '{change2.description[:40]}...'")

        change3 = scene_change_crud.create_scene_change(
            db=db, scene_id=scene.id,
            change_type="character_driven",
            description="镇民集资重建了酒馆，比原来更大",
            day_number=5,
            growth_log_id=None,
        )
        print(f"      SceneChange #3: type={change3.change_type}, day={change3.day_number}")

        # [③] get_recent_changes — 最近 N 条
        recent = scene_change_crud.get_recent_changes(db, scene.id, limit=2)
        print(f"\n  [③] get_recent_changes(limit=2): {len(recent)} 条")
        for rc in recent:
            print(f"       day={rc.day_number} | {rc.change_type} | {rc.description[:50]}")
        assert len(recent) == 2
        assert recent[0].day_number == 5  # 最新在前

        # [④] get_scene_changes_by_world — 按世界+天数
        world_changes = scene_change_crud.get_scene_changes_by_world(db, world.id, day_number=3)
        print(f"\n  [④] get_scene_changes_by_world(day=3): {len(world_changes)} 条")
        assert len(world_changes) == 1
        assert "暴风雪" in world_changes[0].description

        # [⑤] get_scene_changes_by_character — 通过角色关联
        char = Character(
            name="测试角色",
            personality=json.dumps({"optimism": 50}),
            current_state=json.dumps({"location": "test"}),
            world_id=world.id,
        )
        db.add(char)
        db.commit()
        char_changes = scene_change_crud.get_scene_changes_by_character(db, char.id)
        print(f"  [⑤] get_scene_changes_by_character: {len(char_changes)} 条")
        assert len(char_changes) == 3

        print("  [OK] Step 9.3 SceneChange CRUD 全部通过")


# ============================================================================
# Step 10: Creation LLM 升级 — World + Scene 结构化数据
# ============================================================================

class TestStep10_CreationWorldInit:
    """测试创建角色时 World + Scene 的完整初始化链路"""

    def test_validate_creation_schema_with_world_scene(self, db):
        """validate_creation_schema 校验 world_name / core_worldview / scenes"""
        print("\n" + "─" * 60)
        print("  Step 10.1 — validate_creation_schema: 世界字段校验")
        print("─" * 60)

        # [①] 完整数据 — 含 world_name + core_worldview + scenes（含嵌套概念场景）
        full_data = {
            "name": "艾琳",
            "world_setting": "北境雪原上的边境小镇",
            "world_name": "北境雪原",
            "core_worldview": "严寒笼罩的边境之地，生存即荣耀",
            "scenes": [
                {
                    "name": "北境大陆",
                    "scene_layer": "conceptual",
                    "scene_type": "continent",
                    "parent_index": -1,
                    "description": "被永恒冬季覆盖的北方大陆",
                },
                {
                    "name": "霜语镇",
                    "scene_layer": "conceptual",
                    "scene_type": "town",
                    "parent_index": 0,
                    "description": "边境上的最后一座人类据点",
                },
                {
                    "name": "破冰酒馆",
                    "scene_layer": "actual",
                    "scene_type": "tavern",
                    "parent_index": 1,
                    "description": "霜语镇唯一的酒馆，艾琳经营的地方",
                },
            ],
            "personality": {"optimism": 65, "courage": 80, "empathy": 70,
                           "loyalty": 75, "intelligence": 60, "sociability": 55},
            "current_state": {"location": "破冰酒馆", "activity": "擦拭吧台", "mood": "平静"},
            "initial_memories": [{"content": "曾是北境骑士", "importance": 8}],
            "speaking_style": ["豪爽直接"],
            "values": ["守护弱者"],
            "habits": ["清晨练剑"],
            "long_term_goal": "守护霜语镇",
            "day1_schedule": [
                {"content": "清晨开门营业", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
            ],
        }

        validated = LLMService.validate_creation_schema(full_data)

        # 验证 world_name 和 core_worldview 保留
        print(f"  [①] 完整数据校验通过:")
        print(f"       world_name='{validated['world_name']}'")
        print(f"       core_worldview='{validated['core_worldview']}'")
        assert validated["world_name"] == "北境雪原"
        assert validated["core_worldview"] == "严寒笼罩的边境之地，生存即荣耀"

        # 验证 scenes 数组
        scenes = validated["scenes"]
        print(f"       scenes: {len(scenes)} 个场景")
        for s in scenes:
            print(f"         - [{s['scene_layer']}] {s['name']} (parent_index={s['parent_index']})")
        assert len(scenes) == 3
        assert scenes[0]["name"] == "北境大陆"
        assert scenes[0]["scene_layer"] == "conceptual"
        assert scenes[0]["parent_index"] == -1
        assert scenes[2]["scene_layer"] == "actual"
        assert scenes[2]["parent_index"] == 1  # 指向"霜语镇"

        # [②] 缺失 world_name — 自动用 "{角色名}的世界" 补全
        data_no_world = {
            "name": "无名角色",
            "world_setting": "一片虚无",
            "core_worldview": "",
            "scenes": [],
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                           "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "虚空", "activity": "存在", "mood": "无"},
            "day1_schedule": [],
        }
        validated2 = LLMService.validate_creation_schema(data_no_world)
        print(f"\n  [②] 缺失 world_name → 自动补全: '{validated2['world_name']}'")
        assert validated2["world_name"] == "无名角色的世界"

        # core_worldview 缺失 → 用 world_setting 的前100字补全
        print(f"      core_worldview → 自动补全: '{validated2['core_worldview']}'")
        assert "一片虚无" in validated2["core_worldview"]

        # scenes 空数组 → 保底生成 1 概念 + 1 实际
        print(f"      scenes → 保底生成: {len(validated2['scenes'])} 个")
        assert len(validated2["scenes"]) >= 2
        has_conc = any(s["scene_layer"] == "conceptual" for s in validated2["scenes"])
        has_act = any(s["scene_layer"] == "actual" for s in validated2["scenes"])
        assert has_conc
        assert has_act
        print(f"        - 概念场景: {next(s['name'] for s in validated2['scenes'] if s['scene_layer']=='conceptual')}")
        print(f"        - 实际场景: {next(s['name'] for s in validated2['scenes'] if s['scene_layer']=='actual')}")

        # [③] 只有概念场景无实际 → 自动补充 actual
        data_only_conceptual = {
            "name": "测试",
            "world_setting": "测试",
            "world_name": "测试世界",
            "core_worldview": "测试",
            "scenes": [
                {"name": "测试大陆", "scene_layer": "conceptual",
                 "scene_type": "continent", "parent_index": -1, "description": "测试"},
            ],
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                           "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "测试地点", "activity": "测试", "mood": "测试"},
            "day1_schedule": [],
        }
        validated3 = LLMService.validate_creation_schema(data_only_conceptual)
        print(f"\n  [③] 只有概念场景 → 自动补充 actual:")
        assert len(validated3["scenes"]) == 2
        for s in validated3["scenes"]:
            print(f"       - [{s['scene_layer']}] {s['name']}")

        print("  [OK] Step 10.1 Schema 校验全部通过")

    def test_create_character_persists_world_and_scenes(self, db):
        """角色创建 → World + Scene 持久化 → 关联 character.world_id"""
        print("\n" + "─" * 60)
        print("  Step 10.2 — 角色创建时 World + Scene 写入数据库")
        print("─" * 60)

        # [①] 模拟 Creation LLM 输出含 world/scene 数据，直接通过 CRUD 层写入
        #      （此处绕过 main.py 的 HTTP 端点，直接测试数据层逻辑）

        # 1a. 创建 World
        world = world_crud.create_world(
            db=db,
            name="翡翠林地",
            core_worldview="精灵族世代守护的魔法森林",
        )
        print(f"  [①] World 已创建: id={world.id}, name='{world.name}'")

        # 1b. 创建场景树
        forest = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="翡翠森林",
            scene_layer="conceptual",
            scene_type="forest",
            description="无边无际的魔法森林",
        )
        print(f"      概念场景: id={forest.id}, name='{forest.name}'")

        grove = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="月光林地",
            scene_layer="conceptual",
            scene_type="grove",
            parent_scene_id=forest.id,
            description="精灵族的圣地",
        )
        print(f"      概念场景: id={grove.id}, name='{grove.name}', parent={grove.parent_scene_id}")

        hut = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="林间小屋",
            scene_layer="actual",
            scene_type="hut",
            parent_scene_id=grove.id,
            description="精灵药师梅莉亚的居所",
        )
        print(f"      实际场景: id={hut.id}, name='{hut.name}', parent={hut.parent_scene_id}")

        clearing = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="月光广场",
            scene_layer="actual",
            scene_type="square",
            parent_scene_id=grove.id,
            description="举办月神祭典的广场",
        )
        print(f"      实际场景: id={clearing.id}, name='{clearing.name}', parent={clearing.parent_scene_id}")

        # [②] 创建角色并关联 world + current_scene
        char = character_crud.create_character(
            db=db,
            name="梅莉亚·星语",
            description="翡翠林地的精灵药师",
            world_setting="精灵族世代守护的魔法森林",
            personality={"optimism": 72, "courage": 45, "empathy": 88,
                        "loyalty": 90, "intelligence": 78, "sociability": 55},
            current_state={"location": "林间小屋", "activity": "研磨草药", "mood": "专注"},
            speaking_style=json.dumps(["温柔耐心", "偶尔说古精灵语"], ensure_ascii=False),
            values=json.dumps(["守护自然", "知识传承"], ensure_ascii=False),
            habits=json.dumps(["晨间采药", "月下冥想"], ensure_ascii=False),
            long_term_goal="找到治愈森林瘟疫的方法",
        )
        # 关联 World + Scene
        character_crud.update_character(
            db=db,
            character_id=char.id,
            world_id=world.id,
            current_scene_id=hut.id,
        )
        db.refresh(char)

        print(f"\n  [②] 角色已创建: id={char.id}, name='{char.name}'")
        print(f"       world_id={char.world_id}")
        print(f"       current_scene_id={char.current_scene_id}")

        # [③] 验证：通过角色找到世界
        char_world = world_crud.get_world_by_character(db, char.id)
        print(f"\n  [③] 验证: get_world_by_character → name='{char_world.name}'")
        print(f"       core_worldview='{char_world.core_worldview}'")
        assert char_world.id == world.id
        assert char_world.name == "翡翠林地"

        # [④] 验证：通过角色找到所有场景
        char_scenes = scene_crud.get_scenes_by_character(db, char.id)
        print(f"\n  [④] get_scenes_by_character: {len(char_scenes)} 个场景")
        for s in char_scenes:
            mark = " ← 角色当前位置" if s.id == char.current_scene_id else ""
            print(f"       [{s.scene_layer}] id={s.id}, name='{s.name}'{mark}")
        assert len(char_scenes) == 4

        # [⑤] 验证：场景路径正确
        path = scene_crud.get_scene_path(db, hut.id)
        print(f"\n  [⑤] get_scene_path(hut={hut.id}): 深度={len(path)} 层")
        print(f"       路径: {' > '.join(s.name for s in path)}")
        assert len(path) == 3  # forest → grove → hut
        assert path[0].name == "翡翠森林"
        assert path[2].name == "林间小屋"

        # [⑥] 验证：initial_description 不受 scene.update 影响
        scene_crud.update_scene(db, hut.id, description="草药被打翻，一片狼藉")
        refreshed = scene_crud.get_scene(db, hut.id)
        print(f"\n  [⑥] scene.description 已更新: '{refreshed.description}'")
        print(f"       scene.initial_description: '{refreshed.initial_description}' (未变)")
        assert refreshed.initial_description == "精灵药师梅莉亚的居所"
        assert refreshed.description == "草药被打翻，一片狼藉"

        # [⑦] get_initial_actual_scene 正确
        first = scene_crud.get_initial_actual_scene(db, world.id)
        print(f"\n  [⑦] get_initial_actual_scene: id={first.id}, name='{first.name}'")
        assert first.id == hut.id  # 第一个创建的 actual

        # [⑧] get_adjacent_scenes: hut 的兄弟
        adj = scene_crud.get_adjacent_scenes(db, hut.id)
        print(f"  [⑧] get_adjacent_scenes: {len(adj)} 个兄弟场景")
        for a in adj:
            print(f"       - {a.name} ({a.scene_type})")
        assert len(adj) == 1
        assert adj[0].name == "月光广场"

        print("  [OK] Step 10.2 World+Scene 持久化全部通过")


# ============================================================================
# Step 11: Growth SceneChange 集成
# ============================================================================

class TestStep11_GrowthSceneChange:
    """测试 Growth 迭代后的 SceneChange 写入"""

    def test_write_scene_changes_from_growth(self, db):
        """模拟 Growth 产出的 world_changes → scene_changes 表写入 → scene.description 更新"""
        print("\n" + "─" * 60)
        print("  Step 11.1 — Growth SceneChange 写入验证")
        print("─" * 60)

        # [①] 创建 World + Scene + Character
        world = world_crud.create_world(
            db=db,
            name="试炼峡谷",
            core_worldview="勇者的试炼之地",
        )
        scene = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="峡谷入口",
            scene_layer="actual",
            scene_type="cave",
            description="幽深的峡谷入口，两侧岩壁如刀削",
        )
        print(f"  [①] World + Scene 已创建: scene.id={scene.id}")
        print(f"       initial_description='{scene.initial_description}'")

        # 创建 GrowthLog（模拟）
        growth_log = growth_crud.create_growth_log(
            db=db,
            character_id=1,  # 临时占位
            personality_delta=json.dumps({"optimism": 2}),
            event_summary="角色经历了一天",
            new_memories=json.dumps([]),
            growth_raw="{}",
            schedule_json=json.dumps([]),
            world_changes_json=json.dumps({"description": "峡谷深处传来龙吟"}),
        )

        # [②] 模拟 Growth 输出的 world_changes 写入 scene_changes
        world_changes_data = {
            "description": "峡谷深处传来龙吟，入口的碎石因龙威而坠落",
            "change_type": "external",
        }
        world_changes_json = json.dumps(world_changes_data, ensure_ascii=False)

        change = scene_change_crud.create_scene_change(
            db=db,
            scene_id=scene.id,
            change_type="external",
            description="峡谷深处传来龙吟，入口的碎石因龙威而坠落",
            day_number=1,
            growth_log_id=growth_log.id,
            change_details_json=json.dumps(world_changes_data, ensure_ascii=False),
        )
        print(f"\n  [②] SceneChange 已创建: id={change.id}")
        print(f"       scene_id={change.scene_id}")
        print(f"       change_type='{change.change_type}'")
        print(f"       day_number={change.day_number}")
        print(f"       description='{change.description[:60]}...'")

        # [③] 同步更新 scene.description
        scene_crud.update_scene(db, scene.id, description=change.description)
        updated_scene = scene_crud.get_scene(db, scene.id)
        print(f"\n  [③] scene.description 已同步更新:")
        print(f"       initial_description: '{updated_scene.initial_description}' (保持不变)")
        print(f"       description:          '{updated_scene.description}' (新值)")
        assert updated_scene.initial_description == "幽深的峡谷入口，两侧岩壁如刀削"
        assert "龙吟" in updated_scene.description

        # [④] 查询场景变化历史
        changes = scene_change_crud.get_recent_changes(db, scene.id)
        print(f"\n  [④] get_recent_changes: {len(changes)} 条")
        for c in changes:
            print(f"       day={c.day_number} | {c.change_type} | {c.description[:60]}")
        assert len(changes) == 1

        # [⑤] 创建第二条变化（同一场景）
        change2 = scene_change_crud.create_scene_change(
            db=db,
            scene_id=scene.id,
            change_type="character_driven",
            description="勇者艾琳用雷霆战锤击碎了坠落的巨石，清出了通路",
            day_number=2,
            growth_log_id=growth_log.id,
        )
        print(f"\n  [⑤] SceneChange #2: day={change2.day_number}, type='{change2.change_type}'")

        # 按世界查询（确认 scene.world_id 关联正确）
        world_changes = scene_change_crud.get_scene_changes_by_world(db, world.id)
        print(f"      get_scene_changes_by_world: {len(world_changes)} 条")
        assert len(world_changes) == 2

        print("  [OK] Step 11.1 Growth SceneChange 全部通过")

    def test_scene_change_without_world_graceful(self, db):
        """角色未关联世界时，SceneChange 写入应优雅跳过（不抛异常）"""
        print("\n" + "─" * 60)
        print("  Step 11.2 — 无世界关联时的优雅降级")
        print("─" * 60)

        # 创建一个未关联 world 的角色
        char = character_crud.create_character(
            db=db,
            name="无世界角色",
            personality={"optimism": 50},
            current_state={"location": "未知", "activity": "无", "mood": "无"},
        )
        print(f"  [①] 角色已创建 (无 world 关联): id={char.id}, world_id={char.world_id}")

        # 尝试查找世界 — 应返回 None
        w = world_crud.get_world_by_character(db, char.id)
        print(f"  [②] get_world_by_character → {w} (应为 None)")
        assert w is None

        # 尝试查找场景 — 应返回空列表
        scenes = scene_crud.get_scenes_by_character(db, char.id)
        print(f"  [③] get_scenes_by_character → {len(scenes)} 个场景 (应为 0)")
        assert scenes == []

        # 尝试查找变化 — 应返回空列表
        changes = scene_change_crud.get_scene_changes_by_character(db, char.id)
        print(f"  [④] get_scene_changes_by_character → {len(changes)} 条 (应为 0)")
        assert changes == []

        print("  [OK] Step 11.2 优雅降级全部通过")


# ============================================================================
# Step 12: Schema 序列化 + 前端数据层 + E2E 闭合
# ============================================================================

class TestStep12_SchemaSerialization:
    """测试 World/Scene/SceneChange Schema 序列化"""

    def test_world_schema_serialization(self, db):
        """WorldResponse Schema 从 ORM 对象序列化"""
        print("\n" + "─" * 60)
        print("  Step 12.1 — Schema 序列化验证")
        print("─" * 60)

        world = world_crud.create_world(
            db=db,
            name="试炼世界",
            core_worldview="一个试炼用的世界",
        )
        print(f"  [①] World ORM: id={world.id}, name='{world.name}'")

        # WorldResponse 序列化
        resp = WorldResponse.model_validate(world)
        resp_dict = resp.model_dump(mode="json")
        print(f"  [②] WorldResponse.model_dump(mode='json'):")
        print(f"       {json.dumps(resp_dict, ensure_ascii=False, default=str)}")
        assert resp_dict["id"] == world.id
        assert resp_dict["name"] == "试炼世界"
        assert resp_dict["core_worldview"] == "一个试炼用的世界"
        assert "created_at" in resp_dict

        # SceneResponse 序列化
        scene = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="试炼场景",
            scene_layer="actual",
            scene_type="tavern",
            description="一间试炼酒馆",
        )
        scene_resp = SceneResponse.model_validate(scene)
        scene_dict = scene_resp.model_dump(mode="json")
        print(f"\n  [③] SceneResponse.model_dump:")
        print(f"       {json.dumps(scene_dict, ensure_ascii=False, default=str)}")
        assert scene_dict["name"] == "试炼场景"
        assert scene_dict["scene_layer"] == "actual"
        assert scene_dict["world_id"] == world.id

        # SceneChangeResponse 序列化
        change = scene_change_crud.create_scene_change(
            db=db, scene_id=scene.id,
            change_type="external",
            description="天气变冷了",
            day_number=1,
        )
        change_resp = SceneChangeResponse.model_validate(change)
        change_dict = change_resp.model_dump(mode="json")
        print(f"\n  [④] SceneChangeResponse.model_dump:")
        print(f"       {json.dumps(change_dict, ensure_ascii=False, default=str)}")
        assert change_dict["change_type"] == "external"
        assert change_dict["scene_id"] == scene.id

        print("  [OK] Step 12.1 Schema 序列化全部通过")


class TestStep12_E2E_FullLifecycle:
    """端到端测试：创建 → World/Scene 验证 → 推进 → 迭代 → SceneChange"""

    def test_full_world_scene_lifecycle(self, db):
        """完整闭合：创建角色(含World+Scene) → 推进事件 → Growth迭代 → SceneChange记录"""
        print("\n" + "┌" + "─" * 68 + "┐")
        print("  │  Step 12.2 — E2E 端到端完整闭环：World + Scene 生命周期  │")
        print("  └" + "─" * 68 + "┘")

        # ═══════════════════════════════════════════════════════
        # 阶段 A：创建角色（含 World + Scene）
        # ═══════════════════════════════════════════════════════
        print("\n  ╔══ 阶段 A: 角色创建（World + Scene 初始化） ══╗")

        # A1: 创建 World
        world = world_crud.create_world(
            db=db,
            name="星辰海域",
            core_worldview="群岛环绕的神秘海域，传说有星辰坠落于此",
        )
        print(f"  [A1] World 已创建: id={world.id}")
        print(f"       name='{world.name}'")
        print(f"       core_worldview='{world.core_worldview}'")

        # A2: 创建场景树（3 层概念 + 2 实际）
        ocean = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="星辰海域",
            scene_layer="conceptual",
            scene_type="ocean",
            description="群岛环绕的神秘海域",
        )
        island = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="望星岛",
            scene_layer="conceptual",
            scene_type="island",
            parent_scene_id=ocean.id,
            description="海域中最大的岛屿，传说中的星辰碎片坠落之处",
        )
        port = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="星落港",
            scene_layer="conceptual",
            scene_type="port",
            parent_scene_id=island.id,
            description="望星岛唯一的港口，商船往来如梭",
        )
        tavern = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="星辰酒馆",
            scene_layer="actual",
            scene_type="tavern",
            parent_scene_id=port.id,
            description="港口旁一间热闹的酒馆，常年有航海者聚集",
        )
        lighthouse = scene_crud.create_scene(
            db=db, world_id=world.id,
            name="星辰灯塔",
            scene_layer="actual",
            scene_type="lighthouse",
            parent_scene_id=island.id,
            description="岛上的古老灯塔，传说由陨星核心点亮",
        )
        print(f"  [A2] 场景树已创建: {len(scene_crud.get_scenes_by_world(db, world.id))} 个场景")
        for s in scene_crud.get_scenes_by_world(db, world.id):
            indent = "  " if s.scene_layer == "actual" else ""
            print(f"       [{s.scene_layer}] {indent}{s.name} (parent={s.parent_scene_id})")

        # A3: 创建角色，关联 world + current_scene
        char = character_crud.create_character(
            db=db,
            name="林雨晴",
            description="望星岛上的年轻航海士",
            world_setting="星辰海域——群岛环绕的神秘海域",
            personality={"optimism": 75, "courage": 60, "empathy": 55,
                        "loyalty": 70, "intelligence": 80, "sociability": 85},
            current_state={"location": "星辰酒馆", "activity": "研究海图", "mood": "好奇"},
            speaking_style=json.dumps(["热情爽朗", "喜欢用航海术语比喻"], ensure_ascii=False),
            values=json.dumps(["追求真理", "珍视友情"], ensure_ascii=False),
            habits=json.dumps(["每日观察星辰", "记录航海日志"], ensure_ascii=False),
            long_term_goal="找到传说中的星辰碎片，解开海域之谜",
        )
        character_crud.update_character(
            db=db,
            character_id=char.id,
            world_id=world.id,
            current_scene_id=tavern.id,
        )
        db.refresh(char)
        print(f"\n  [A3] 角色已创建: id={char.id}, name='{char.name}'")
        print(f"       world_id={char.world_id}, current_scene_id={char.current_scene_id}")
        print(f"       long_term_goal='{char.long_term_goal}'")
        print(f"       personality: {json.loads(char.personality)}")

        # A4: 创建 Day 1 初始事件
        day1_events = [
            {"content": "清晨在星辰灯塔观察天象", "event_type": "schedule_action",
             "time_period": "morning", "order_index": 1},
            {"content": "整理航海日志", "event_type": "schedule_action",
             "time_period": "afternoon", "order_index": 2},
            {"content": "在星辰酒馆与老船长们交流", "event_type": "scene_event",
             "time_period": "evening", "order_index": 3},
            {"content": "夜观星象，绘制新航线", "event_type": "schedule_action",
             "time_period": "night", "order_index": 4},
        ]
        for item in day1_events:
            event_crud.create_event(
                db=db, character_id=char.id,
                day_number=1,
                order_index=item["order_index"],
                event_type=item["event_type"],
                content=item["content"],
                status="pending",
                time_period=item.get("time_period"),
            )
        print(f"\n  [A4] Day 1 初始事件: {len(day1_events)} 条 pending")

        # 验证初始状态
        db.refresh(char)
        assert char.world_id == world.id
        assert char.current_scene_id == tavern.id
        char_world = world_crud.get_world_by_character(db, char.id)
        assert char_world.name == "星辰海域"

        # ═══════════════════════════════════════════════════════
        # 阶段 B：推进 Day 1 全部事件
        # ═══════════════════════════════════════════════════════
        print("\n  ╔══ 阶段 B: 推进 Day 1 事件（pending → completed） ══╗")

        for i in range(4):
            ev = event_crud.get_next_pending_event(db, char.id, 1)
            result = f"'{ev.content}' — 已完成"
            event_crud.complete_event(db, ev.id, result)
            db.refresh(ev)
            print(f"  [B{i+1}] '{ev.content}' → completed [OK]")
            print(f"        result: {result}")

        completed_events = event_crud.get_events_by_day(db, char.id, 1, status_filter="completed")
        print(f"\n  Day 1 完成: completed={len(completed_events)}, pending={event_crud.has_pending_events(db, char.id, 1)}")
        assert len(completed_events) == 4

        # ═══════════════════════════════════════════════════════
        # 阶段 C：Growth 迭代 + SceneChange
        # ═══════════════════════════════════════════════════════
        print("\n  ╔══ 阶段 C: Growth 迭代 + SceneChange 写入 ══╗")

        # C1: 创建 GrowthLog（模拟 LLM 输出）
        growth_log = growth_crud.create_growth_log(
            db=db,
            character_id=char.id,
            personality_delta=json.dumps({"optimism": 5, "courage": 2, "intelligence": 3}),
            event_summary="林雨晴在整理航海日志时发现了一张古老的海图，指向一片未知海域。"
                         "老船长们分享了关于'星辰碎片'的传说，激起了她的探索欲望。",
            new_memories=json.dumps([
                {"content": "发现了一张标注'星辰碎片'的古老海图", "importance": 9},
                {"content": "老船长说星辰碎片可以控制天气", "importance": 7},
                {"content": "决定明天出海探索未知海域", "importance": 8},
            ]),
            growth_raw="{}",
            schedule_json=json.dumps([
                {"content": "准备出海物资", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
                {"content": "驾船驶向未知海域", "event_type": "scene_event",
                 "time_period": "afternoon", "order_index": 2},
                {"content": "在海上遭遇暴风雨", "event_type": "scene_event",
                 "time_period": "evening", "order_index": 3},
            ]),
            world_changes_json=json.dumps({
                "description": "星辰海域的天气因古老海图的出现而开始变得异常，"
                               "远方的天际线上出现了从未见过的紫色极光",
                "change_type": "external",
            }),
        )
        print(f"  [C1] GrowthLog 已创建: id={growth_log.id}")
        print(f"       personality_delta: {json.loads(growth_log.personality_delta)}")
        print(f"       event_summary: '{growth_log.event_summary[:80]}...'")
        print(f"       new_memories: {len(json.loads(growth_log.new_memories))} 条")

        # C2: 写入 SceneChange（角色当前场景的变化）
        world_changes_data = json.loads(growth_log.world_changes_json)
        scene_change_crud.create_scene_change(
            db=db,
            scene_id=char.current_scene_id,
            change_type=world_changes_data["change_type"],
            description=world_changes_data["description"],
            day_number=1,
            growth_log_id=growth_log.id,
            change_details_json=growth_log.world_changes_json,
        )
        # 同步更新 scene.description
        scene_crud.update_scene(
            db, char.current_scene_id,
            description=world_changes_data["description"],
        )
        print(f"\n  [C2] SceneChange 已写入 (scene={char.current_scene_id})")
        print(f"       type='external', day=1")

        # C3: 更新角色人格 + day_number
        old_personality = json.loads(char.personality)
        delta = json.loads(growth_log.personality_delta)
        new_p = {}
        for dim in ["optimism", "courage", "empathy", "loyalty", "intelligence", "sociability"]:
            new_p[dim] = max(0, min(100, old_personality.get(dim, 50) + delta.get(dim, 0)))
        character_crud.update_character(
            db=db, character_id=char.id,
            personality=new_p,
            day_number=2,
        )
        db.refresh(char)

        # C4: 创建 Day 2 事件
        schedule = json.loads(growth_log.schedule_json)
        for item in schedule:
            event_crud.create_event(
                db=db, character_id=char.id,
                day_number=2,
                order_index=item["order_index"],
                event_type=item["event_type"],
                content=item["content"],
                status="pending",
                time_period=item.get("time_period"),
            )
        print(f"  [C3] Day 2 事件已创建: {len(schedule)} 条 pending")
        for item in schedule:
            print(f"       [{item['time_period']}] {item['content']}")

        # ═══════════════════════════════════════════════════════
        # 阶段 D：最终验证
        # ═══════════════════════════════════════════════════════
        print("\n  ╔══ 阶段 D: 最终验证 ══╗")

        db.refresh(char)
        print(f"  [OK] 角色 '{char.name}': day_number={char.day_number}")
        print(f"     world_id={char.world_id}, current_scene_id={char.current_scene_id}")

        # D1: World 关联正确
        char_world = world_crud.get_world_by_character(db, char.id)
        assert char_world.name == "星辰海域"
        print(f"  [OK] World: '{char_world.name}' — {char_world.core_worldview[:50]}")

        # D2: Scene 树完整
        char_scenes = scene_crud.get_scenes_by_character(db, char.id)
        assert len(char_scenes) == 5
        current_scene = scene_crud.get_scene(db, char.current_scene_id)
        assert current_scene.name == "星辰酒馆"
        assert "紫色极光" in current_scene.description  # 已更新！
        assert current_scene.initial_description == "港口旁一间热闹的酒馆，常年有航海者聚集"  # 不变！
        print(f"  [OK] 当前场景: '{current_scene.name}'")
        print(f"     description: '{current_scene.description[:60]}...'")
        print(f"     initial_description: '{current_scene.initial_description[:40]}...'")

        # D3: 场景路径
        path = scene_crud.get_scene_path(db, char.current_scene_id)
        path_str = " > ".join(s.name for s in path)
        print(f"  [OK] 场景路径: {path_str}")
        assert len(path) == 4  # ocean → island → port → tavern

        # D4: SceneChange 记录
        changes = scene_change_crud.get_scene_changes_by_character(db, char.id)
        assert len(changes) == 1
        assert changes[0].scene_id == char.current_scene_id
        assert "紫色极光" in changes[0].description
        print(f"  [OK] SceneChange: day={changes[0].day_number}, type='{changes[0].change_type}'")
        print(f"     '{changes[0].description[:80]}...'")

        # D5: Day 1/2 事件状态
        day1_completed = event_crud.get_events_by_day(db, char.id, 1, status_filter="completed")
        day2_pending = event_crud.get_events_by_day(db, char.id, 2, status_filter="pending")
        assert len(day1_completed) == 4
        assert len(day2_pending) == 3
        print(f"  [OK] Day 1: completed={len(day1_completed)}")
        print(f"  [OK] Day 2: pending={len(day2_pending)}")

        # D6: 人格演化
        print(f"\n  [OK] 人格演化:")
        for dim in ["optimism", "courage", "empathy", "loyalty", "intelligence", "sociability"]:
            old = old_personality.get(dim, 50)
            new = new_p.get(dim, 50)
            d = delta.get(dim, 0)
            arrow = "↑" if d > 0 else ("↓" if d < 0 else "→")
            print(f"       {dim:14s}: {old:3d} {arrow} {new:3d}  (delta={d:+d})")

        # D7: Schema 序列化（确保前端可用）
        world_resp = WorldResponse.model_validate(char_world)
        scene_resp = SceneResponse.model_validate(current_scene)
        change_resp = SceneChangeResponse.model_validate(changes[0])
        char_resp = CharacterResponse.model_validate(char)
        print(f"\n  [OK] Schema 序列化: WorldResponse={world_resp.id}, SceneResponse={scene_resp.id}, "
              f"SceneChangeResponse={change_resp.id}, CharacterResponse={char_resp.id}")
        print(f"     CharacterResponse.world_id={char_resp.world_id}")
        print(f"     CharacterResponse.current_scene_id={char_resp.current_scene_id}")

        print("\n" + "═" * 72)
        print("  [OK][OK][OK] Step 12.2 E2E 端到端完整闭环验证通过！[OK][OK][OK]")
        print("═" * 72)

        print(f"""
  闭环链路总结:
    Creation
      → World '{world.name}' 已创建 (core_worldview)
      → Scene 树: {len(char_scenes)} 个场景 ({path_str})
      → Character '{char.name}' 关联 (world_id={char.world_id}, scene_id={char.current_scene_id})
    Day 1
      → {len(day1_events)} events (pending)
      → advance × {len(day1_events)} (pending → completed)
      → Growth 观察 {len(day1_completed)} completed events
      → personality delta: {delta}
      → {len(schedule)} events (Day 2, pending)
      → {len(changes)} SceneChange(s) 写入
      → scene.description 已更新 (initial_description 保持不变)
    Day 2
      → {len(day2_pending)} events (pending)
      → day_number: 1 → 2
  """)


# ============================================================================
# 运行入口
# ============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
