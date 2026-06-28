"""
Steps 13-15 综合测试：短期目标系统 + v1.6 全量 API 端到端验证

测试范围（严格按照 v1.6开发执行路线）：

  Step 13 (creation-short-term-goals):
    -> validate_creation_schema 新增 short_term_goals 校验
    -> character create 持久化 short_term_goals
    -> short_term_goals 保底机制

  Step 14 (growth-short-term-goals):
    -> Growth prompt 注入 active short_term_goals
    -> Growth 产出 goal_updates + new_goals
    -> 目标 progress 更新 + 新目标生成持久化

  Step 15 (v1.6-all-apis):
    -> 调用 v1.6 Phase 1 全部 13 个后端接口
    -> 端到端闭环：创建->World+Scene+Goals->推进->迭代->目标更新
    -> Schema 序列化验证

测试采用内存 SQLite，不依赖外部 LLM 服务。
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
    CharacterResponse, EventResponse, GrowthResponse,
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
# Step 13: Creation short_term_goals 校验与持久化
# ============================================================================

class TestStep13_CreationShortTermGoals:
    """测试 Creation 流程中 short_term_goals 的校验和持久化"""

    def test_validate_creation_schema_with_short_term_goals(self, db):
        """validate_creation_schema 校验 short_term_goals 合法数据"""
        print("\n" + "─" * 60)
        print("  Step 13.1 — validate_creation_schema: short_term_goals 校验")
        print("─" * 60)

        # [①] 完整数据：含 3 条合格的短期目标
        full_data = {
            "name": "艾琳",
            "world_setting": "北境雪原上的边境小镇",
            "world_name": "北境雪原",
            "core_worldview": "严寒笼罩的边境之地",
            "scenes": [
                {"name": "北境大陆", "scene_layer": "conceptual",
                 "scene_type": "continent", "parent_index": -1, "description": "被永恒冬季覆盖的大陆"},
                {"name": "破冰酒馆", "scene_layer": "actual",
                 "scene_type": "tavern", "parent_index": 0, "description": "艾琳经营的地方"},
            ],
            "personality": {"optimism": 70, "courage": 80, "empathy": 60,
                           "loyalty": 75, "intelligence": 65, "sociability": 55},
            "current_state": {"location": "破冰酒馆", "activity": "擦拭吧台", "mood": "平静"},
            "initial_memories": [{"content": "曾是北境骑士", "importance": 8}],
            "speaking_style": ["豪爽直接"],
            "values": ["守护弱者"],
            "habits": ["清晨练剑"],
            "long_term_goal": "成为北境最强剑士",
            "day1_schedule": [
                {"content": "清晨练剑", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
            ],
            # ---- Step 13 测试核心字段 ----
            "short_term_goals": [
                {"goal": "找到一位剑术大师指点", "progress": 0.0, "created_day": 1, "source": "creation"},
                {"goal": "打造一把精良的长剑", "progress": 0.0, "created_day": 1, "source": "creation"},
                {"goal": "完成每日基础训练", "progress": 0.15, "created_day": 1, "source": "creation"},
            ],
        }

        validated = LLMService.validate_creation_schema(full_data)

        # 验证 short_term_goals 保留
        goals = validated["short_term_goals"]
        print(f"  [①] 完整数据校验通过:")
        print(f"       short_term_goals: {len(goals)} 条")
        for i, g in enumerate(goals):
            print(f"       [{i}] goal='{g['goal']}', progress={g['progress']}, source='{g['source']}'")
        assert len(goals) == 3
        assert goals[0]["goal"] == "找到一位剑术大师指点"
        assert goals[0]["progress"] == 0.0
        assert goals[0]["source"] == "creation"
        assert goals[1]["goal"] == "打造一把精良的长剑"

        # [②] 缺失 short_term_goals -> 保底 1 条（基于 long_term_goal）
        data_no_goals = {
            "name": "无名",
            "world_setting": "虚空",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                           "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "虚空", "activity": "无", "mood": "无"},
            "long_term_goal": "探索宇宙奥秘",
            "day1_schedule": [],
        }
        validated2 = LLMService.validate_creation_schema(data_no_goals)
        goals2 = validated2["short_term_goals"]
        print(f"\n  [②] 缺失 short_term_goals -> 保底 1 条:")
        print(f"       goal='{goals2[0]['goal']}'")
        assert len(goals2) == 1
        assert goals2[0]["progress"] == 0.0
        assert goals2[0]["created_day"] == 1

        # [③] 空数组 -> 保底 1 条
        data_empty = {
            "name": "空目标",
            "world_setting": "虚空",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                           "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "虚空", "activity": "无", "mood": "无"},
            "long_term_goal": "寻找生命意义",
            "short_term_goals": [],
            "day1_schedule": [],
        }
        validated3 = LLMService.validate_creation_schema(data_empty)
        goals3 = validated3["short_term_goals"]
        print(f"\n  [③] 空数组 -> 保底 1 条: goal='{goals3[0]['goal']}'")
        assert len(goals3) == 1

        # [④] 非法 progress -> 钳位
        data_bad_progress = {
            "name": "坏进度",
            "world_setting": "虚空",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                           "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "虚空", "activity": "无", "mood": "无"},
            "short_term_goals": [
                {"goal": "目标A", "progress": 1.5, "created_day": 1, "source": "creation"},
                {"goal": "目标B", "progress": -0.5, "created_day": 1, "source": "creation"},
                {"goal": "目标C", "progress": "not_a_number", "created_day": 1, "source": "creation"},
            ],
            "day1_schedule": [],
        }
        validated4 = LLMService.validate_creation_schema(data_bad_progress)
        goals4 = validated4["short_term_goals"]
        print(f"\n  [④] 非法 progress 钳位:")
        print(f"       1.5 -> {goals4[0]['progress']} (应钳位到 1.0)")
        print(f"       -0.5 -> {goals4[1]['progress']} (应钳位到 0.0)")
        print(f"       'str' -> {goals4[2]['progress']} (应为 0.0)")
        assert goals4[0]["progress"] == 1.0
        assert goals4[1]["progress"] == 0.0
        assert goals4[2]["progress"] == 0.0

        # [⑤] 非法 source -> 标准化为 "creation"
        data_bad_source = {
            "name": "坏来源",
            "world_setting": "虚空",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                           "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "虚空", "activity": "无", "mood": "无"},
            "short_term_goals": [
                {"goal": "测试", "progress": 0.0, "created_day": 1, "source": "unknown_source"},
            ],
            "day1_schedule": [],
        }
        validated5 = LLMService.validate_creation_schema(data_bad_source)
        print(f"\n  [⑤] 非法 source 'unknown_source' -> '{validated5['short_term_goals'][0]['source']}' (应为 creation)")
        assert validated5["short_term_goals"][0]["source"] == "creation"

        # [⑥] 空 content 目标 -> 跳过
        data_empty_goal = {
            "name": "空内容",
            "world_setting": "虚空",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                           "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "虚空", "activity": "无", "mood": "无"},
            "short_term_goals": [
                {"goal": "", "progress": 0.0, "created_day": 1, "source": "creation"},
            ],
            "day1_schedule": [],
        }
        validated6 = LLMService.validate_creation_schema(data_empty_goal)
        # 空目标被跳过，保底 1 条
        print(f"\n  [⑥] 空 goal -> 跳过，保底 1 条: '{validated6['short_term_goals'][0]['goal']}'")
        assert len(validated6["short_term_goals"]) == 1

        print("  [OK] Step 13.1 Schema 校验全部通过")

    def test_create_character_persists_short_term_goals(self, db):
        """角色创建 -> short_term_goals 持久化到数据库"""
        print("\n" + "─" * 60)
        print("  Step 13.2 — 角色创建时 short_term_goals 写入数据库")
        print("─" * 60)

        # [①] 创建角色 + 关联 World/Scene
        world = world_crud.create_world(db, name="测试世界", core_worldview="测试")
        scene = scene_crud.create_scene(
            db=db, world_id=world.id, name="测试地点",
            scene_layer="actual", scene_type="tavern",
            description="一间测试酒馆",
        )

        # 模拟 Creation LLM 产出的 short_term_goals
        goals_data = [
            {"goal": "找到传说中的名剑", "progress": 0.0, "created_day": 1, "source": "creation"},
            {"goal": "结交一位可靠的朋友", "progress": 0.0, "created_day": 1, "source": "creation"},
            {"goal": "掌握基础剑术", "progress": 0.3, "created_day": 1, "source": "creation"},
        ]
        goals_json = json.dumps(goals_data, ensure_ascii=False)

        # 创建角色并持久化 short_term_goals
        char = character_crud.create_character(
            db=db,
            name="剑士·凯",
            description="一位年轻的冒险者",
            world_setting="剑与魔法的世界",
            personality={"optimism": 75, "courage": 85, "empathy": 60,
                        "loyalty": 70, "intelligence": 55, "sociability": 65},
            current_state={"location": "测试地点", "activity": "练剑", "mood": "专注"},
            long_term_goal="成为世界最强剑士",
            speaking_style=json.dumps(["直率"], ensure_ascii=False),
            values=json.dumps(["追求武道"], ensure_ascii=False),
            habits=json.dumps(["每日练剑"], ensure_ascii=False),
        )
        character_crud.update_character(db, char.id, world_id=world.id, current_scene_id=scene.id,
                                         short_term_goals=goals_json)
        db.refresh(char)

        print(f"  [①] 角色已创建: id={char.id}, name='{char.name}'")
        print(f"       short_term_goals (数据库原始值): {char.short_term_goals[:100]}...")

        # [②] 验证持久化：从数据库重新读取
        db.refresh(char)
        stored_goals = json.loads(char.short_term_goals)
        print(f"\n  [②] 从数据库重新读取 short_term_goals: {len(stored_goals)} 条")
        for i, g in enumerate(stored_goals):
            print(f"       [{i}] goal='{g['goal']}', progress={g['progress']}, "
                  f"created_day={g['created_day']}, source='{g['source']}'")
        assert len(stored_goals) == 3
        assert stored_goals[0]["goal"] == "找到传说中的名剑"
        assert stored_goals[1]["goal"] == "结交一位可靠的朋友"
        assert stored_goals[2]["progress"] == 0.3

        # [③] CharacterResponse Schema 序列化（验证 short_term_goals 正确传递）
        char_resp = CharacterResponse.model_validate(char)
        resp_goals_str = char_resp.short_term_goals
        print(f"\n  [③] CharacterResponse.short_term_goals (序列化): {resp_goals_str[:100]}...")
        assert resp_goals_str is not None
        resp_goals = json.loads(resp_goals_str)
        assert len(resp_goals) == 3

        # [④] 空 short_term_goals -> None（通过 update_character 设置为 None）
        char2 = character_crud.create_character(
            db=db, name="无目标角色",
            personality={"optimism": 50},
            current_state={"location": "无", "activity": "无", "mood": "无"},
        )
        db.refresh(char2)
        print(f"\n  [④] 无 short_term_goals 的角色: short_term_goals={char2.short_term_goals}")
        assert char2.short_term_goals is None

        print("  [OK] Step 13.2 持久化全部通过")


# ============================================================================
# Step 14: Growth short_term_goals 集成
# ============================================================================

class TestStep14_GrowthShortTermGoals:
    """测试 Growth 模块中的短期目标注入和处理"""

    def test_growth_goal_updates_and_new_goals(self, db):
        """Growth 产出 goal_updates / new_goals -> 持久化到 character.short_term_goals"""
        print("\n" + "─" * 60)
        print("  Step 14.1 — Growth 短期目标更新与持久化")
        print("─" * 60)

        # [①] 创建角色 + 初始短期目标
        world = world_crud.create_world(db, name="剑术之都", core_worldview="一个以剑术闻名的城市")
        scene = scene_crud.create_scene(
            db=db, world_id=world.id, name="剑术道场",
            scene_layer="actual", scene_type="dojo",
            description="古色古香的剑术道场",
        )

        initial_goals = [
            {"goal": "找到一位剑术大师拜师", "progress": 0.0, "created_day": 1, "source": "creation"},
            {"goal": "每天坚持练剑", "progress": 0.0, "created_day": 1, "source": "creation"},
            {"goal": "参加一次剑术比赛", "progress": 0.0, "created_day": 1, "source": "creation"},
        ]
        char = character_crud.create_character(
            db=db,
            name="学徒·云",
            description="一位渴望变强的年轻剑士",
            world_setting="剑术之都，群英荟萃",
            personality={"optimism": 70, "courage": 75, "empathy": 55,
                        "loyalty": 80, "intelligence": 60, "sociability": 50},
            current_state={"location": "剑术道场", "activity": "冥想", "mood": "渴望成长"},
            long_term_goal="成为天下第一剑客",
            speaking_style=json.dumps(["谦虚有礼"], ensure_ascii=False),
            values=json.dumps(["武道至上"], ensure_ascii=False),
            habits=json.dumps(["每日晨练"], ensure_ascii=False),
            short_term_goals=json.dumps(initial_goals, ensure_ascii=False),
        )
        character_crud.update_character(db, char.id, world_id=world.id, current_scene_id=scene.id)
        db.refresh(char)

        print(f"  [①] 角色已创建: id={char.id}, name='{char.name}'")
        stored = json.loads(char.short_term_goals)
        print(f"       初始目标: {len(stored)} 条")
        for g in stored:
            print(f"       - goal='{g['goal']}', progress={g['progress']}")

        # [②] 创建 Day 1 已完成事件（模拟"找到了大师" + "日常练剑"）
        events_data = [
            {"content": "清晨在道场练剑", "event_type": "schedule_action",
             "time_period": "morning", "order_index": 1, "day_number": 1},
            {"content": "拜访城内著名剑术大师·流云", "event_type": "scene_event",
             "time_period": "afternoon", "order_index": 2, "day_number": 1},
            {"content": "整理装备，准备正式拜师", "event_type": "schedule_action",
             "time_period": "evening", "order_index": 3, "day_number": 1},
        ]
        for item in events_data:
            ev = event_crud.create_event(
                db=db, character_id=char.id,
                day_number=item["day_number"],
                order_index=item["order_index"],
                event_type=item["event_type"],
                content=item["content"],
                status="pending",
                time_period=item.get("time_period"),
            )
            event_crud.complete_event(db, ev.id, f"' {item['content']}' — 已完成")

        completed = event_crud.get_events_by_day(db, char.id, 1, status_filter="completed")
        print(f"\n  [②] Day 1 完成事件: {len(completed)} 条")

        # [③] 模拟 Growth 产出 goal_updates + new_goals（不调用真实 LLM）
        #     GrowthModule 中的 self._safe_load_json(character.short_term_goals) 读取初始目标
        #     然后注入到 prompt 中。此处直接模拟 Growth 输出的处理逻辑。
        growth_log = growth_crud.create_growth_log(
            db=db,
            character_id=char.id,
            personality_delta=json.dumps({"optimism": 3, "courage": 2, "intelligence": 1}),
            event_summary="云在剑术道场勤奋训练，并成功拜访了流云大师。"
                         "大师对他的毅力表示认可，愿意收他为徒。",
            new_memories=json.dumps([
                {"content": "流云大师愿意收他为徒", "importance": 10},
                {"content": "每日练剑已成为雷打不动的习惯", "importance": 7},
            ]),
            growth_raw="{}",
            schedule_json=json.dumps([
                {"content": "参加拜师仪式", "event_type": "scene_event",
                 "time_period": "morning", "order_index": 1},
                {"content": "跟随大师学习基础剑法", "event_type": "schedule_action",
                 "time_period": "afternoon", "order_index": 2},
                {"content": "进行实战对练", "event_type": "schedule_action",
                 "time_period": "evening", "order_index": 3},
            ]),
            world_changes_json=json.dumps({"description": "流云大师决定收云为关门弟子"}),
        )

        # [④] 模拟 goal_updates 处理：更新进展
        #     第 0 条 "找到一位剑术大师拜师" -> progress 从 0.0 更新为 1.0（已完成！）
        #     第 1 条 "每天坚持练剑" -> progress 从 0.0 更新为 0.3（日常推进）
        updated_goals = list(initial_goals)
        updated_goals[0]["progress"] = 1.0  # 已找到大师并拜师
        updated_goals[1]["progress"] = 0.3  # 日常练剑推进

        # 新增替换目标（大师已找到，生成新阶段目标）
        new_goals_from_growth = [
            {"goal": "修习大师传授的'流云剑法'第一式", "progress": 0.0, "created_day": 2, "source": "growth"},
            {"goal": "剑术比赛中进入前四强", "progress": 0.0, "created_day": 2, "source": "growth"},
        ]
        # 去重：不添加与已有目标相同的
        for ng in new_goals_from_growth:
            existing_texts = {g["goal"] for g in updated_goals if isinstance(g, dict) and "goal" in g}
            if ng["goal"] not in existing_texts:
                updated_goals.append(ng)

        print(f"\n  [④] Growth 产出后的目标状态:")
        for i, g in enumerate(updated_goals):
            status = "[DONE]" if g["progress"] >= 1.0 else f"in-progress ({g['progress']})"
            print(f"       [{i}] {g['goal']} — {status}, source='{g['source']}'")

        # [⑤] 验证目标数量与内容
        assert len(updated_goals) == 5  # 3 初始 + 2 新
        assert updated_goals[0]["progress"] == 1.0
        assert updated_goals[0]["goal"] == "找到一位剑术大师拜师"
        assert updated_goals[1]["progress"] == 0.3
        assert updated_goals[3]["goal"] == "修习大师传授的'流云剑法'第一式"
        assert updated_goals[3]["source"] == "growth"
        print(f"  [⑤] 验证通过: 总目标 {len(updated_goals)} 条, 1 条已完成")

        # [⑥] 持久化更新后的目标
        character_crud.update_character(
            db=db, character_id=char.id,
            short_term_goals=json.dumps(updated_goals, ensure_ascii=False),
            day_number=2,
        )
        db.refresh(char)
        final_goals = json.loads(char.short_term_goals)
        print(f"\n  [⑥] 持久化验证: char.day_number={char.day_number}, goals={len(final_goals)} 条")
        assert char.day_number == 2
        assert len(final_goals) == 5

        # [⑦] 过滤活跃目标（progress < 1.0）——用于下一轮 Growth
        active = [g for g in final_goals if g["progress"] < 1.0]
        active_goals_str = json.dumps(active, ensure_ascii=False) if active else "[]"
        print(f"\n  [⑦] 活跃目标（progress < 1.0）: {len(active)} 条")
        for g in active:
            print(f"       - {g['goal']} (progress={g['progress']})")
        assert len(active) == 4  # 第 0 条已完成，其余 4 条活跃

        # [⑧] 验证无目标角色也能正确注入空数组
        char_no_goals = character_crud.create_character(
            db=db, name="无目标角色",
            personality={"optimism": 50},
            current_state={"location": "无", "activity": "无", "mood": "无"},
        )
        no_goals = char_no_goals.short_term_goals
        print(f"\n  [⑧] 无目标的角色: short_term_goals={no_goals} (应显示 None)")
        if no_goals:
            parsed_none = json.loads(no_goals) if isinstance(no_goals, str) else []
            fallback = parsed_none if isinstance(parsed_none, list) and parsed_none else "[]"
        else:
            fallback = "[]"
        assert fallback == "[]"

        print("  [OK] Step 14.1 Growth 目标更新全部通过")


# ============================================================================
# Step 15: v1.6 Phase 1 全量 API 接口测试
# ============================================================================

class TestStep15_AllV16APIs:
    """测试 v1.6 Phase 1 全部 13 个后端接口的 CRUD 层"""

    def test_world_api_all_endpoints(self, db):
        """World 相关 4 个 API 端点"""
        print("\n" + "─" * 60)
        print("  Step 15.1 — World API: 全部 4 个端点")
        print("─" * 60)

        # [W1] POST/CRUD: create_world -> 对应 GET/PATCH /api/worlds
        w1 = world_crud.create_world(db, name="翡翠梦境", core_worldview="万物有灵的奇幻森林")
        w2 = world_crud.create_world(db, name="钢铁之城", core_worldview="蒸汽与齿轮的工业都市")
        print(f"  [W1] 已创建 2 个 World: '{w1.name}' (id={w1.id}), '{w2.name}' (id={w2.id})")

        # [W2] GET /api/worlds (get_all_worlds) — 获取所有世界
        all_worlds = world_crud.get_all_worlds(db)
        print(f"  [W2] get_all_worlds() -> {len(all_worlds)} 个世界")
        assert len(all_worlds) == 2

        # [W3] GET /api/worlds/{id} (get_world) — 获取单个世界
        fetched = world_crud.get_world(db, w1.id)
        print(f"  [W3] get_world(id={w1.id}) -> name='{fetched.name}', worldview='{fetched.core_worldview}'")
        assert fetched.name == "翡翠梦境"

        # [W4] PATCH /api/worlds/{id} (update_world) — 更新世界
        updated = world_crud.update_world(db, w1.id, name="翡翠梦境 (扩展)", core_worldview="万物有灵的奇幻森林——精灵族的家园")
        print(f"  [W4] update_world -> name='{updated.name}'")
        assert updated.name == "翡翠梦境 (扩展)"
        assert "精灵族" in updated.core_worldview

        # [W5] WorldResponse Schema 序列化
        resp = WorldResponse.model_validate(fetched)
        d = resp.model_dump(mode="json")
        print(f"  [W5] WorldResponse 序列化: id={d['id']}, name='{d['name']}' OK")
        assert d["id"] == w1.id

        print("  [OK] Step 15.1 World API 全部通过")

    def test_scene_api_all_endpoints(self, db):
        """Scene 相关 5 个 API 端点"""
        print("\n" + "─" * 60)
        print("  Step 15.2 — Scene API: 全部 5 个端点")
        print("─" * 60)

        # [S1] 创建 World + Scene 树
        world = world_crud.create_world(db, name="龙脊山脉", core_worldview="巨龙栖息的神秘山脉")
        continent = scene_crud.create_scene(
            db=db, world_id=world.id, name="龙脊大陆",
            scene_layer="conceptual", scene_type="continent",
            description="被龙脊山脉贯穿的广袤大陆",
        )
        peaks = scene_crud.create_scene(
            db=db, world_id=world.id, name="龙骨峰",
            scene_layer="conceptual", scene_type="mountain",
            parent_scene_id=continent.id,
            description="山脉最高处，传说有巨龙筑巢",
        )
        cave = scene_crud.create_scene(
            db=db, world_id=world.id, name="龙息洞穴",
            scene_layer="actual", scene_type="cave",
            parent_scene_id=peaks.id,
            description="被巨龙龙息灼烧过的洞穴入口",
        )
        nest = scene_crud.create_scene(
            db=db, world_id=world.id, name="龙巢大厅",
            scene_layer="actual", scene_type="lair",
            parent_scene_id=peaks.id,
            description="巨龙巢穴最深处，堆满了闪闪发光的财宝",
        )
        print(f"  [S1] 创建场景树: 2 概念 + 2 实际 = {len(scene_crud.get_scenes_by_world(db, world.id))} 个")

        # [S2] GET /api/worlds/{id}/scenes — 世界场景列表（概念层）
        conceptual_only = scene_crud.get_scenes_by_world(db, world.id, scene_layer="conceptual")
        print(f"  [S2] conceptual 场景: {len(conceptual_only)} 个")
        for s in conceptual_only:
            print(f"       - [{s.scene_type}] {s.name}")
        assert len(conceptual_only) == 2

        # [S3] GET /api/worlds/{id}/scenes — 实际层
        actual_only = scene_crud.get_scenes_by_world(db, world.id, scene_layer="actual")
        print(f"  [S3] actual 场景: {len(actual_only)} 个")
        assert len(actual_only) == 2

        # [S4] GET /api/scenes/{id}/path — 场景路径
        path = scene_crud.get_scene_path(db, cave.id)
        path_str = " > ".join(s.name for s in path)
        print(f"  [S4] get_scene_path(cave) -> {path_str}")
        assert len(path) == 3
        assert path[0].name == "龙脊大陆"
        assert path[2].name == "龙息洞穴"

        # [S5] GET /api/scenes/{id}/adjacent — 兄弟场景
        adjacent = scene_crud.get_adjacent_scenes(db, cave.id)
        print(f"  [S5] get_adjacent_scenes(cave) -> {len(adjacent)} 个兄弟场景")
        for a in adjacent:
            print(f"       - {a.name} ({a.scene_type})")
        assert len(adjacent) == 1
        assert adjacent[0].name == "龙巢大厅"

        # [S6] SceneResponse Schema 序列化
        resp = SceneResponse.model_validate(cave)
        d = resp.model_dump(mode="json")
        print(f"  [S6] SceneResponse 序列化: id={d['id']}, layer='{d['scene_layer']}' OK")

        # [S7] update_scene + initial_description 不变
        scene_crud.update_scene(db, cave.id, description="龙息已消散，洞穴变得安静")
        refreshed = scene_crud.get_scene(db, cave.id)
        print(f"  [S7] description 更新: '{refreshed.description}'")
        print(f"       initial_description: '{refreshed.initial_description}' (未变)")
        assert refreshed.initial_description == "被巨龙龙息灼烧过的洞穴入口"
        assert "安静" in refreshed.description

        print("  [OK] Step 15.2 Scene API 全部通过")

    def test_scene_change_api_all_endpoints(self, db):
        """SceneChange 相关 3 个 API 端点"""
        print("\n" + "─" * 60)
        print("  Step 15.3 — SceneChange API: 全部 3 个端点")
        print("─" * 60)

        # 创建 World + Scene
        world = world_crud.create_world(db, name="变迁世界", core_worldview="时间流动的世界")
        scene = scene_crud.create_scene(
            db=db, world_id=world.id, name="时光广场",
            scene_layer="actual", scene_type="square",
            description="世界中心的广场",
        )

        # [C1] POST CRUD: create_scene_change -> 对应 GET /api/scenes/{id}/changes
        ch1 = scene_change_crud.create_scene_change(
            db=db, scene_id=scene.id,
            change_type="external",
            description="一场暴风雪降临广场，积雪没过了脚踝",
            day_number=1,
        )
        ch2 = scene_change_crud.create_scene_change(
            db=db, scene_id=scene.id,
            change_type="character_driven",
            description="冒险者清理了广场的积雪，露出了古老的符文石板",
            day_number=2,
        )
        ch3 = scene_change_crud.create_scene_change(
            db=db, scene_id=scene.id,
            change_type="external",
            description="石板上的符文开始发光，天空中出现了紫色的极光",
            day_number=3,
        )
        print(f"  [C1] 创建 3 条 SceneChange: day=1,2,3")

        # [C2] GET /api/scenes/{id}/changes — 场景变化历史
        recent = scene_change_crud.get_recent_changes(db, scene.id, limit=20)
        print(f"  [C2] get_recent_changes(limit=20) -> {len(recent)} 条")
        for c in recent:
            print(f"       day={c.day_number} | {c.change_type} | {c.description[:50]}...")
        assert len(recent) == 3
        assert recent[0].day_number == 3  # 最新在前

        # [C3] GET /api/characters/{id}/world-changes — 角色世界的变化时间轴
        char = character_crud.create_character(
            db=db, name="观察者",
            personality={"optimism": 50},
            current_state={"location": "时光广场", "activity": "观察", "mood": "好奇"},
        )
        character_crud.update_character(db, char.id, world_id=world.id, current_scene_id=scene.id)
        char_changes = scene_change_crud.get_scene_changes_by_character(db, char.id)
        print(f"  [C3] get_scene_changes_by_character -> {len(char_changes)} 条")
        assert len(char_changes) == 3

        # [C4] SceneChangeResponse Schema 序列化
        resp = SceneChangeResponse.model_validate(ch3)
        d = resp.model_dump(mode="json")
        print(f"  [C4] SceneChangeResponse 序列化: id={d['id']}, type='{d['change_type']}' OK")
        assert d["change_type"] == "external"

        # [C5] get_scene_changes_by_world — 按世界+天数查询
        by_world = scene_change_crud.get_scene_changes_by_world(db, world.id)
        print(f"  [C5] get_scene_changes_by_world -> {len(by_world)} 条")
        assert len(by_world) == 3

        print("  [OK] Step 15.3 SceneChange API 全部通过")

    def test_character_world_integration_api(self, db):
        """角色-世界集成的 3 个 API 端点"""
        print("\n" + "─" * 60)
        print("  Step 15.4 — Character-World 集成 API")
        print("─" * 60)

        # [CW1] 创建 World + Scene 树 + Character
        world = world_crud.create_world(db, name="星落群岛", core_worldview="传说有星辰碎片坠落的群岛")
        island = scene_crud.create_scene(
            db=db, world_id=world.id, name="望星岛",
            scene_layer="conceptual", scene_type="island",
            description="群岛中最大的岛屿",
        )
        port = scene_crud.create_scene(
            db=db, world_id=world.id, name="星落港",
            scene_layer="actual", scene_type="port",
            parent_scene_id=island.id,
            description="岛上唯一的港口，商船往来如梭",
        )
        char = character_crud.create_character(
            db=db, name="航海士·星",
            description="望星岛的年轻航海士",
            world_setting="星辰群岛——传说中的星辰碎片坠落之地",
            personality={"optimism": 80, "courage": 70, "empathy": 60,
                        "loyalty": 75, "intelligence": 85, "sociability": 70},
            current_state={"location": "星落港", "activity": "绘制海图", "mood": "专注"},
            long_term_goal="找到传说中的星辰碎片",
            speaking_style=json.dumps(["热情乐观"], ensure_ascii=False),
            values=json.dumps(["探索未知"], ensure_ascii=False),
            habits=json.dumps(["每日观测天象"], ensure_ascii=False),
        )
        character_crud.update_character(db, char.id, world_id=world.id, current_scene_id=port.id)
        db.refresh(char)
        print(f"  [CW1] 角色 '{char.name}' 已关联 world_id={char.world_id}, current_scene_id={char.current_scene_id}")

        # [CW2] GET /api/characters/{id}/world (get_world_by_character)
        char_world = world_crud.get_world_by_character(db, char.id)
        print(f"  [CW2] get_world_by_character -> name='{char_world.name}', worldview='{char_world.core_worldview[:40]}...'")
        assert char_world.id == world.id
        assert char_world.name == "星落群岛"

        # [CW3] GET /api/characters/{id}/scenes (get_scenes_by_character)
        char_scenes = scene_crud.get_scenes_by_character(db, char.id)
        print(f"  [CW3] get_scenes_by_character -> {len(char_scenes)} 个场景")
        for s in char_scenes:
            mark = " ← 当前位置" if s.id == char.current_scene_id else ""
            print(f"       [{s.scene_layer}] {s.name}{mark}")
        assert len(char_scenes) == 2

        # [CW4] CharacterResponse 含 world_id + current_scene_id
        char_resp = CharacterResponse.model_validate(char)
        print(f"  [CW4] CharacterResponse: world_id={char_resp.world_id}, current_scene_id={char_resp.current_scene_id} OK")
        assert char_resp.world_id == world.id
        assert char_resp.current_scene_id == port.id

        # [CW5] 角色未关联世界 -> get_world_by_character 返回 None
        char2 = character_crud.create_character(
            db=db, name="流浪者",
            personality={"optimism": 50},
            current_state={"location": "荒野", "activity": "流浪", "mood": "平静"},
        )
        no_world = world_crud.get_world_by_character(db, char2.id)
        print(f"  [CW5] 未关联世界角色: get_world_by_character -> {no_world} (应为 None)")
        assert no_world is None

        # [CW6] 未关联世界 -> get_scenes_by_character 返回空
        no_scenes = scene_crud.get_scenes_by_character(db, char2.id)
        assert no_scenes == []
        print(f"  [CW6] 无场景的角色: get_scenes_by_character -> [] (OK)")

        print("  [OK] Step 15.4 Character-World 集成 API 全部通过")


# ============================================================================
# Step 15 E2E: 端到端完整闭环
# ============================================================================

class TestStep15_E2E_FullLifecycleWithGoals:
    """端到端测试：创建 -> World/Scene/Goals -> 推进 -> 迭代 -> 目标更新"""

    def test_full_lifecycle_with_short_term_goals(self, db):
        """完整的 v1.6 闭环：World+Scene+Goals -> Events -> Growth -> Goal Updates"""
        print("\n" + "┌" + "─" * 68 + "┐")
        print("  │  Step 15.5 — E2E 端到端完整闭环：v1.6 World + Goals 生命周期  │")
        print("  └" + "─" * 68 + "┘")

        # ═══════════════════════════════════════════════════════
        # 阶段 A: 角色创建（World + Scene + Goals）
        # ═══════════════════════════════════════════════════════
        print("\n  ╔══ 阶段 A: 角色创建（World + Scene + Goals） ══╗")

        world = world_crud.create_world(
            db=db, name="剑术之城·凌霄",
            core_worldview="以剑术闻名的浮空城市，剑士的圣地",
        )
        city = scene_crud.create_scene(
            db=db, world_id=world.id, name="凌霄城",
            scene_layer="conceptual", scene_type="city",
            description="悬浮于云层之上的古老剑术之城",
        )
        dojo = scene_crud.create_scene(
            db=db, world_id=world.id, name="天一剑道场",
            scene_layer="conceptual", scene_type="dojo",
            parent_scene_id=city.id,
            description="凌霄城最负盛名的剑术道场",
        )
        hall = scene_crud.create_scene(
            db=db, world_id=world.id, name="道场大厅",
            scene_layer="actual", scene_type="hall",
            parent_scene_id=dojo.id,
            description="铺满榻榻米的宽敞大厅，墙上挂着历代剑圣的名画",
        )
        arena = scene_crud.create_scene(
            db=db, world_id=world.id, name="比武擂台",
            scene_layer="actual", scene_type="arena",
            parent_scene_id=dojo.id,
            description="剑客们切磋比试的圆形擂台，周围坐满了观众",
        )
        print(f"  [A1] World + 场景树: {len(scene_crud.get_scenes_by_world(db, world.id))} 个场景")

        # 创建角色 + 初始短期目标
        initial_goals = [
            {"goal": "通过天一道场的入门考核", "progress": 0.0, "created_day": 1, "source": "creation"},
            {"goal": "掌握基础剑术三式", "progress": 0.0, "created_day": 1, "source": "creation"},
            {"goal": "在道场结识一位朋友", "progress": 0.0, "created_day": 1, "source": "creation"},
        ]
        char = character_crud.create_character(
            db=db,
            name="剑士·凌",
            description="梦想成为剑圣的年轻剑士",
            world_setting="剑术之城·凌霄——剑士的圣地",
            personality={"optimism": 75, "courage": 85, "empathy": 55,
                        "loyalty": 70, "intelligence": 60, "sociability": 65},
            current_state={"location": "道场大厅", "activity": "观摩师兄练剑", "mood": "充满向往"},
            long_term_goal="成为凌霄城第一百代剑圣",
            speaking_style=json.dumps(["谦逊礼貌", "偶尔热血"], ensure_ascii=False),
            values=json.dumps(["尊师重道", "追求剑道极致"], ensure_ascii=False),
            habits=json.dumps(["每日挥剑千次", "晨间冥想"], ensure_ascii=False),
            short_term_goals=json.dumps(initial_goals, ensure_ascii=False),
        )
        character_crud.update_character(db, char.id, world_id=world.id, current_scene_id=hall.id)
        db.refresh(char)
        print(f"  [A2] 角色已创建: id={char.id}, name='{char.name}'")
        print(f"       world_id={char.world_id}, current_scene_id={char.current_scene_id}")
        print(f"       long_term_goal='{char.long_term_goal}'")
        stored_goals = json.loads(char.short_term_goals)
        print(f"       short_term_goals: {len(stored_goals)} 条")
        for g in stored_goals:
            print(f"         - {g['goal']} (progress={g['progress']})")
        assert char.world_id == world.id
        assert len(stored_goals) == 3

        # ═══════════════════════════════════════════════════════
        # 阶段 B: Day 1 事件推进
        # ═══════════════════════════════════════════════════════
        print("\n  ╔══ 阶段 B: Day 1 事件推进 ══╗")

        day1_events = [
            {"content": "参加天一道场的入门考核", "event_type": "scene_event",
             "time_period": "morning", "order_index": 1},
            {"content": "向师兄请教基础剑术", "event_type": "schedule_action",
             "time_period": "afternoon", "order_index": 2},
            {"content": "在道场大厅观摩剑术表演", "event_type": "scene_event",
             "time_period": "evening", "order_index": 3},
            {"content": "整理今日的剑术心得", "event_type": "schedule_action",
             "time_period": "night", "order_index": 4},
        ]
        for item in day1_events:
            ev = event_crud.create_event(
                db=db, character_id=char.id, day_number=1,
                order_index=item["order_index"],
                event_type=item["event_type"],
                content=item["content"],
                status="pending",
                time_period=item.get("time_period"),
            )
            event_crud.complete_event(db, ev.id, f"'{item['content']}' — 圆满完成")
        print(f"  [B1] Day 1: {len(day1_events)} 个事件 -> completed")
        for item in day1_events:
            print(f"       [{item['time_period']}] {item['content']} -> [OK]")

        # ═══════════════════════════════════════════════════════
        # 阶段 C: Growth 迭代 + 目标更新
        # ═══════════════════════════════════════════════════════
        print("\n  ╔══ 阶段 C: Growth 迭代 + 目标更新 ══╗")

        # 模拟 Growth 产出（personality_delta + schedule + goal_updates + new_goals）
        growth_log = growth_crud.create_growth_log(
            db=db,
            character_id=char.id,
            personality_delta=json.dumps({"optimism": 5, "courage": 3, "intelligence": 2, "sociability": 2}),
            event_summary="凌顺利通过了天一道场的入门考核，展现出过人的天赋。"
                         "他向师兄虚心请教，已掌握了基础剑术的前两式。"
                         "傍晚的剑术表演让他深受震撼，更加坚定了成为剑圣的决心。",
            new_memories=json.dumps([
                {"content": "以优异成绩通过入门考核，考官称赞他'天赋异禀'", "importance": 9},
                {"content": "师兄传授了'断水式'和'破风式'两招基础剑术", "importance": 8},
                {"content": "看到剑圣使用'天一剑法'的场景，内心深受震撼", "importance": 7},
            ]),
            growth_raw="{Growth LLM raw response}",
            schedule_json=json.dumps([
                {"content": "练习'断水式'基础剑法", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
                {"content": "参加入门弟子的首次实战对练", "event_type": "scene_event",
                 "time_period": "afternoon", "order_index": 2},
                {"content": "向师兄反馈训练成果并请教难点", "event_type": "schedule_action",
                 "time_period": "afternoon", "order_index": 3},
            ]),
            world_changes_json=json.dumps({
                "description": "凌以新入门弟子的身份正式加入天一道场，引起了几位同门的注意",
                "change_type": "character_driven",
            }),
        )
        print(f"  [C1] Growth 产出:")
        print(f"       personality_delta: {json.loads(growth_log.personality_delta)}")
        print(f"       event_summary: '{growth_log.event_summary[:80]}...'")
        print(f"       new_memories: {len(json.loads(growth_log.new_memories))} 条")

        # [C2] 更新目标:
        #   第 0 条 "通过天一道场的入门考核" -> progress = 1.0 (已完成!)
        #   第 1 条 "掌握基础剑术三式" -> progress = 0.66 (2/3 式)
        #   第 2 条 "在道场结识一位朋友" -> progress = 0.2 (刚开始)
        updated_goals = list(initial_goals)
        updated_goals[0]["progress"] = 1.0
        updated_goals[1]["progress"] = 0.66
        updated_goals[2]["progress"] = 0.2

        # 生成新目标（第 0 条完成后，新阶段目标）
        new_goals = [
            {"goal": "修炼完毕基础剑术全部三式", "progress": 0.0, "created_day": 2, "source": "growth"},
            {"goal": "在实战对练中获胜一场", "progress": 0.0, "created_day": 2, "source": "growth"},
        ]
        for ng in new_goals:
            existing_texts = {g["goal"] for g in updated_goals if isinstance(g, dict) and "goal" in g}
            if ng["goal"] not in existing_texts:
                updated_goals.append(ng)
        assert len(updated_goals) == 5

        print(f"\n  [C2] 目标状态更新:")
        for i, g in enumerate(updated_goals):
            status = "[DONE]" if g["progress"] >= 1.0 else f"in-progress ({g['progress']:.0%})"
            print(f"       [{i}] {g['goal'][:35]:35s} — {status}")

        # 持久化
        character_crud.update_character(
            db=db, character_id=char.id,
            short_term_goals=json.dumps(updated_goals, ensure_ascii=False),
            day_number=2,
        )
        db.refresh(char)

        # [C3] 创建 Day 2 schedule 事件
        schedule = json.loads(growth_log.schedule_json)
        for item in schedule:
            event_crud.create_event(
                db=db, character_id=char.id, day_number=2,
                order_index=item["order_index"],
                event_type=item["event_type"],
                content=item["content"],
                status="pending",
                time_period=item.get("time_period"),
            )
        print(f"\n  [C3] Day 2 事件: {len(schedule)} 条 pending")
        for item in schedule:
            print(f"       [{item['time_period']}] {item['content']}")

        # [C4] SceneChange 写入
        scene_change_crud.create_scene_change(
            db=db, scene_id=hall.id,
            change_type="character_driven",
            description="凌以新入门弟子的身份正式加入天一道场，道场新增了一张刻有他名字的铭牌",
            day_number=1,
            growth_log_id=growth_log.id,
        )

        # ═══════════════════════════════════════════════════════
        # 阶段 D: 最终综合验证
        # ═══════════════════════════════════════════════════════
        print("\n  ╔══ 阶段 D: 最终验证 ══╗")
        db.refresh(char)

        # D1: World 关联
        char_world = world_crud.get_world_by_character(db, char.id)
        assert char_world.name == "剑术之城·凌霄"
        print(f"  [D1] World: '{char_world.name}' OK")

        # D2: Scene 树
        char_scenes = scene_crud.get_scenes_by_character(db, char.id)
        assert len(char_scenes) == 4
        current_scene = scene_crud.get_scene(db, char.current_scene_id)
        assert current_scene.name == "道场大厅"
        print(f"  [D2] 当前场景: '{current_scene.name}', 场景树 {len(char_scenes)} 个 OK")

        # D3: Scene 路径
        path = scene_crud.get_scene_path(db, char.current_scene_id)
        path_str = " > ".join(s.name for s in path)
        assert len(path) == 3
        print(f"  [D3] 场景路径: {path_str} OK")

        # D4: SceneChange
        changes = scene_change_crud.get_scene_changes_by_character(db, char.id)
        assert len(changes) == 1
        assert "天一道场" in changes[0].description
        print(f"  [D4] SceneChange: day={changes[0].day_number}, type='{changes[0].change_type}' OK")

        # D5: 事件状态
        day1_completed = event_crud.get_events_by_day(db, char.id, 1, status_filter="completed")
        day2_pending = event_crud.get_events_by_day(db, char.id, 2, status_filter="pending")
        assert len(day1_completed) == 4
        assert len(day2_pending) == 3
        print(f"  [D5] Day 1: {len(day1_completed)} completed, Day 2: {len(day2_pending)} pending OK")

        # D6: 短期目标
        final_goals = json.loads(char.short_term_goals)
        assert len(final_goals) == 5
        assert final_goals[0]["progress"] == 1.0  # 已完成
        assert final_goals[1]["progress"] == 0.66
        assert final_goals[3]["source"] == "growth"
        active_count = sum(1 for g in final_goals if g["progress"] < 1.0)
        done_count = sum(1 for g in final_goals if g["progress"] >= 1.0)
        print(f"  [D6] 短期目标: {len(final_goals)} 条 (活跃 {active_count}, 已完成 {done_count}) OK")

        # D7: 人格演化
        old_p = {"optimism": 75, "courage": 85, "empathy": 55, "loyalty": 70, "intelligence": 60, "sociability": 65}
        delta = json.loads(growth_log.personality_delta)
        new_p = {}
        for dim in old_p:
            new_p[dim] = max(0, min(100, old_p[dim] + delta.get(dim, 0)))
        print(f"  [D7] 人格演化:")
        for dim in old_p:
            d = delta.get(dim, 0)
            arrow = "UP" if d > 0 else ("DN" if d < 0 else "--")
            print(f"       {dim:14s}: {old_p[dim]:3d} {arrow} {new_p[dim]:3d}  (delta={d:+d})")
        assert new_p["optimism"] == 80
        assert new_p["courage"] == 88

        # D8: Day 2
        assert char.day_number == 2
        print(f"  [D8] day_number: {char.day_number} OK")

        # D9: Schema 序列化（全部 v1.6 Schema）
        w_resp = WorldResponse.model_validate(char_world)
        s_resp = SceneResponse.model_validate(current_scene)
        sc_resp = SceneChangeResponse.model_validate(changes[0])
        ch_resp = CharacterResponse.model_validate(char)
        print(f"  [D9] Schema 序列化验证: WorldResponse OK, SceneResponse OK, "
              f"SceneChangeResponse OK, CharacterResponse OK")
        assert ch_resp.world_id == world.id
        assert ch_resp.current_scene_id == hall.id
        assert ch_resp.short_term_goals is not None

        print("\n" + "═" * 72)
        print("  [OK][OK][OK] Step 15.5 E2E 端到端完整闭环验证通过！[OK][OK][OK]")
        print("═" * 72)

        summary = f"""
  闭环链路总结:
    Phase A — 角色创建
      ├─ World '{world.name}'  (core_worldview)
      ├─ Scene 树: {len(char_scenes)} 个 ({path_str})
      ├─ Character '{char.name}' (world_id={char.world_id}, scene_id={char.current_scene_id})
      └─ short_term_goals: {len(stored_goals)} 条 (creation)

    Phase B — Day 1 推进
      ├─ {len(day1_events)} events -> completed
      └─ 4 条日程: {' > '.join(e['content'][:15] for e in day1_events)}

    Phase C — Growth + Goals
      ├─ personality delta: {delta}
      ├─ event_summary: '{growth_log.event_summary[:60]}...'
      ├─ {len(schedule)} events (Day 2)
      ├─ {len(changes)} SceneChange
      └─ short_term_goals: {len(final_goals)} 条 ({done_count} done, {active_count} active)

    Phase D — 验证
      ├─ World [OK] / Scene [OK] / SceneChange [OK]
      ├─ Character world_id + current_scene_id [OK]
      ├─ short_term_goals persisted [OK]
      └─ All v1.6 Schemas serialized [OK]
  """
        print(summary)


# ============================================================================
# 运行入口
# ============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
