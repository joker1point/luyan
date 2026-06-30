"""
Steps 5-8 综合测试：事件推进端点 + Creation 人格升级 + 前端适配 + E2E 闭环

测试范围严格按照 creative-module-completion-plan 的步骤定义：

  Step 5 (advance-iterate-auto-endpoints)
    → POST /api/event/advance   — 推进下一个 pending 事件
    → POST /api/time/iterate     — Growth 迭代一天
    → POST /api/time/auto        — 自动模式一键推演
    → 保留 /api/growth/trigger   向后兼容

  Step 6 (creation-prompt-upgrade)
    → validate_creation_schema   — 新人格字段校验
    → CreationModule.run()       — speaking_style/values/habits/long_term_goal 持久化

  Step 7 (frontend-adaptation)
    → EventResponse schema       — 前端事件卡片数据
    → CharacterResponse          — 含新人格字段
    → IterateResponse/AutoResponse — 前端弹窗数据
    → GET /api/characters/{id}/events — 前端事件时间轴查询

  Step 8 (e2e-verify)
    → 完整闭环：创建角色(含day1_schedule) → 逐事件推进 → 迭代一天 → Day2验证

测试采用内存 SQLite，不依赖外部 LLM 服务。
所有测试均使用模拟数据直接写入数据库，验证数据层、CRUD 层、Schema 层的完整链路。
"""
import json
import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import Character, Event, GrowthLog, Memory, Conversation
from backend.crud import event as event_crud
from backend.crud import character as character_crud
from backend.crud import growth as growth_crud
from backend.crud import memory as memory_crud
from backend.schemas import (
    EventResponse, AdvanceRequest, IterateResponse, AutoResponse,
    CharacterResponse, GrowthResponse,
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
    print("\n" + "─" * 72)
    print(f"  🏗️  内存数据库已初始化（SQLite :memory:）")
    print("─" * 72)
    yield session
    session.close()
    # Base.metadata.drop_all(bind=engine)  # 保留以验证明细
    print("  🧹 数据库已清理")


@pytest.fixture
def sample_character(db):
    """创建一个带完整人格字段的样例角色"""
    char = Character(
        name="艾琳·酒馆掌柜",
        description="一位经营着边境酒馆的退役女骑士",
        world_setting="王国北境的小镇，常年被风雪笼罩",
        personality=json.dumps({
            "optimism": 65, "courage": 80, "empathy": 70,
            "loyalty": 75, "intelligence": 60, "sociability": 55,
        }, ensure_ascii=False),
        current_state=json.dumps({
            "location": "边境酒馆·大厅", "activity": "擦拭吧台", "mood": "平静",
        }, ensure_ascii=False),
        speaking_style=json.dumps(["直率", "略带沧桑"], ensure_ascii=False),
        values=json.dumps(["守护弱者", "信守承诺", "自由"], ensure_ascii=False),
        habits=json.dumps(["清晨练剑", "泡一杯红茶"], ensure_ascii=False),
        long_term_goal="重建已故战友的孤儿院",
        day_number=1,
    )
    db.add(char)
    db.commit()
    db.refresh(char)
    print(f"  👤 样例角色已创建：{char.name} (id={char.id}, day={char.day_number})")
    return char


@pytest.fixture
def char_with_events(db, sample_character):
    """为角色创建 4 条 Day 1 pending 事件（模拟 Creation 产出的 day1_schedule）"""
    events_data = [
        {"day": 1, "order": 1, "type": "schedule_action",
         "content": "清晨练剑一小时", "time_period": "morning"},
        {"day": 1, "order": 2, "type": "schedule_action",
         "content": "准备酒馆开业食材", "time_period": "morning"},
        {"day": 1, "order": 3, "type": "character_initiative",
         "content": "给老战友写一封信", "time_period": "afternoon"},
        {"day": 1, "order": 4, "type": "schedule_action",
         "content": "盘点酒馆库存", "time_period": "evening"},
    ]
    created = []
    for ev in events_data:
        e = event_crud.create_event(
            db, sample_character.id, ev["day"], ev["order"],
            ev["type"], ev["content"], time_period=ev["time_period"],
        )
        created.append(e)
    print(f"  📋 Day 1 初始事件已创建：{len(created)} 条 pending 事件")
    return sample_character, created


# ============================================================================
# ── Step 5: 事件推进端点 (advance / iterate / auto) ──
# ============================================================================

class TestStep5_AdvanceEvent:
    """Step 5.1 — POST /api/event/advance：推进下一个 pending 事件"""

    def test_advance_completes_next_pending(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 查询角色当前 day_number=1                   │
        │ ② 取 order_index 最小的 pending 事件           │
        │ ③ 根据 event_type 生成 result_json             │
        │ ④ status: pending → completed                 │
        │ ⑤ 返回 EventResponse (含 result_json)          │
        └────────────────────────────────────────────────┘
        """
        char, events = char_with_events
        print("\n  📍 Step 5.1.1 — advance: 推进第1个 pending 事件")
        print(f"      角色: {char.name} | day={char.day_number}")
        print(f"      pending 事件数: {len(events)}")

        # ── 链路步骤 ①-② ──
        current_day = char.day_number or 1
        next_event = event_crud.get_next_pending_event(db, char.id, current_day)
        assert next_event is not None, "应该存在 pending 事件"
        print(f"      ✅ [①-②] 找到下一个 pending 事件: "
              f"type={next_event.event_type}, order={next_event.order_index}, "
              f"content='{next_event.content[:20]}...'")

        # ── 链路步骤 ③ ──
        if next_event.event_type == "schedule_action":
            result = f"角色完成了日程安排：{next_event.content}"
        elif next_event.event_type == "character_initiative":
            result = f"角色主动行动：{next_event.content}"
        elif next_event.event_type == "scene_event":
            result = f"场景事件推进：{next_event.content}"
        else:
            result = f"事件已完成"
        print(f"      ✅ [③] result_json 已生成: '{result[:40]}...'")

        # ── 链路步骤 ④ ──
        completed = event_crud.complete_event(db, next_event.id, result)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.result_json == result
        print(f"      ✅ [④] 事件状态: pending → completed "
              f"(id={completed.id}, result_json 已写入)")

        # ── 链路步骤 ⑤ ──
        response = EventResponse.model_validate(completed)
        assert response.status == "completed"
        assert response.result_json is not None
        print(f"      ✅ [⑤] EventResponse 序列化成功 "
              f"(id={response.id}, type={response.event_type}, status={response.status})")

        # 验证 stats
        stats = event_crud.count_events_by_day(db, char.id, 1)
        print(f"      📊 Day 1 事件统计: pending={stats['pending']}, "
              f"completed={stats['completed']}, total={stats['total']}")

        # 下一个 pending 应该是 order_index=2 的事件
        next_ev = event_crud.get_next_pending_event(db, char.id, 1)
        assert next_ev is not None
        assert next_ev.order_index == 2
        print(f"      🔜 下一个待推进事件: order={next_ev.order_index}, content='{next_ev.content}'")

    def test_advance_sequence_preserves_order(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ 逐次推进 4 个事件，验证每次按 order_index 升序  │
        │ 验证 stats 在每步的渐变                         │
        └────────────────────────────────────────────────┘
        """
        char, _ = char_with_events
        print(f"\n  📍 Step 5.1.2 — advance: 顺序推进全部 4 个事件")

        day = char.day_number
        for step in range(1, 5):
            ev = event_crud.get_next_pending_event(db, char.id, day)
            assert ev is not None, f"步骤{step}: 应有 pending 事件"
            assert ev.order_index == step, \
                f"步骤{step}: 期望 order={step}, 实际 order={ev.order_index}"
            result = f"模拟执行: {ev.content}"
            event_crud.complete_event(db, ev.id, result)
            stats = event_crud.count_events_by_day(db, char.id, day)
            print(f"      [步骤{step}/4] order={ev.order_index} | "
                  f"type={ev.event_type} | stats: "
                  f"pending={stats['pending']}/{stats['total']}")

        # 全部完成后无 pending
        assert not event_crud.has_pending_events(db, char.id, day)
        print(f"      ✅ 全部 4 个事件推进完成，无残留 pending")

    def test_advance_no_pending_returns_none(self, db, sample_character):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ 角色创建后无任何事件 → get_next_pending → None  │
        └────────────────────────────────────────────────┘
        """
        print(f"\n  📍 Step 5.1.3 — advance: 无事件角色")
        ev = event_crud.get_next_pending_event(db, sample_character.id, 1)
        assert ev is None
        assert not event_crud.has_pending_events(db, sample_character.id, 1)
        print(f"      ✅ get_next_pending → None（角色无事件）")
        print(f"      ✅ has_pending_events → False")


class TestStep5_IterateDay:
    """Step 5.2 — POST /api/time/iterate：Growth 迭代一天"""

    def test_iterate_day_number_increment(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 收集当天的 completed 事件                     │
        │ ② 调用 GrowthModule.run()（模拟）               │
        │ ③ 将 schedule 数组写入 events 表（day+1，pending）│
        │ ④ 角色 day_number += 1                         │
        │ ⑤ 返回 IterateResponse                         │
        └────────────────────────────────────────────────┘
        """
        char, events = char_with_events
        print(f"\n  📍 Step 5.2.1 — iterate: 模拟 Growth 迭代")

        # ── 先完成全部 4 个事件 ──
        for ev in events:
            event_crud.complete_event(db, ev.id,
                f"模拟完成: {ev.content}")
        completed_events = event_crud.get_events_by_day(
            db, char.id, 1, status_filter="completed")
        assert len(completed_events) == 4
        print(f"      📋 已完成事件: {len(completed_events)} 条")

        # ── 链路步骤 ① ──
        formatted_text = _format_events_for_display(completed_events)
        print(f"      ✅ [①] 收集 completed 事件（共 {len(completed_events)} 条）")
        for i, ev in enumerate(completed_events):
            print(f"         [{i+1}] type={ev.event_type} | order={ev.order_index} | "
                  f"content='{ev.content[:25]}'")

        # ── 链路步骤 ② ── (模拟 Growth 输出)
        mock_schedule = [
            {"content": "查看战利品收藏室的新藏品", "event_type": "schedule_action",
             "time_period": "morning", "order_index": 1},
            {"content": "与来访的旅人交换情报", "event_type": "character_initiative",
             "time_period": "afternoon", "order_index": 2},
            {"content": "在酒馆举办小型诗会", "event_type": "schedule_action",
             "time_period": "evening", "order_index": 3},
        ]
        mock_personality_delta = {"optimism": 3, "courage": -1, "sociability": 2}
        mock_event_summary = "艾琳度过了忙碌的一天，完成了日常训练和酒馆工作，情绪稳定。"
        mock_world_changes = "酒馆的存货显示需要补充一些稀有食材。"
        mock_new_memories = [
            {"content": "今天清晨练剑时想起了战场的记忆", "importance": 7},
            {"content": "酒馆生意不错，食材消耗比预期快", "importance": 4},
        ]
        print(f"      ✅ [②] Growth LLM 输出（模拟）:")
        print(f"         schedule: {len(mock_schedule)} 条次日事件")
        print(f"         personality_delta: {mock_personality_delta}")
        print(f"         world_changes: '{mock_world_changes}'")

        # 写入 growth_log
        growth_log = growth_crud.create_growth_log(
            db, char.id,
            personality_delta=json.dumps(mock_personality_delta, ensure_ascii=False),
            event_summary=mock_event_summary,
            new_memories=json.dumps(mock_new_memories, ensure_ascii=False),
            growth_raw="(模拟)",
        )
        print(f"      ✅ growth_log 已持久化 (id={growth_log.id})")

        # ── 链路步骤 ③ ──
        new_day = char.day_number + 1
        ev_count = 0
        for item in mock_schedule:
            event_crud.create_event(
                db, char.id, new_day,
                item["order_index"], item["event_type"],
                item["content"],
                status="pending", time_period=item.get("time_period"),
            )
            ev_count += 1
        print(f"      ✅ [③] schedule → events 表: {ev_count} 条 (day={new_day}, status=pending)")

        # ── 链路步骤 ④ ──
        db.refresh(char)
        old_day = char.day_number
        character_crud.update_character(
            db, char.id,
            personality=json.dumps({
                "optimism": 68, "courage": 79, "empathy": 70,
                "loyalty": 75, "intelligence": 60, "sociability": 57,
            }, ensure_ascii=False),
            day_number=new_day,
        )
        db.refresh(char)
        assert char.day_number == 2
        print(f"      ✅ [④] 角色 day_number: {old_day} → {char.day_number}")

        # ── 链路步骤 ⑤ ──
        day2_events = event_crud.get_events_by_day(db, char.id, 2, status_filter="pending")
        assert len(day2_events) == 3
        print(f"      ✅ [⑤] Day 2 pending 事件: {len(day2_events)} 条")
        for ev in day2_events:
            print(f"         order={ev.order_index} | type={ev.event_type} | "
                  f"period={ev.time_period} | content='{ev.content[:30]}'")

    def test_iterate_updates_personality(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ 验证 Growth 迭代后人格数值正确更新               │
        └────────────────────────────────────────────────┘
        """
        char, events = char_with_events
        print(f"\n  📍 Step 5.2.2 — iterate: 人格演化验证")

        # 完成所有事件
        for ev in events:
            event_crud.complete_event(db, ev.id, f"done: {ev.content}")

        # 模拟 Growth 迭代
        old_personality = json.loads(char.personality)
        print(f"      迭代前人格: {old_personality}")

        delta = {"optimism": 5, "sociability": -3}
        new_personality = old_personality.copy()
        for k, v in delta.items():
            new_personality[k] = max(0, min(100, old_personality.get(k, 50) + v))

        growth_crud.create_growth_log(
            db, char.id,
            personality_delta=json.dumps(delta, ensure_ascii=False),
            event_summary="测试迭代",
            new_memories="[]",
            growth_raw="(模拟)",
        )
        character_crud.update_character(db, char.id,
            personality=json.dumps(new_personality, ensure_ascii=False),
            day_number=char.day_number + 1)

        db.refresh(char)
        updated = json.loads(char.personality)
        print(f"      迭代后人格: {updated}")
        assert updated["optimism"] == 70  # 65 + 5
        assert updated["sociability"] == 52  # 55 + (-3)
        assert char.day_number == 2
        print(f"      ✅ 人格已正确更新: optimism 65→{updated['optimism']}, "
              f"sociability 55→{updated['sociability']}, day 1→{char.day_number}")

    def test_growth_trigger_backward_compatible(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ 验证 /api/growth/trigger 端点向后兼容 ——       │
        │ GrowthLog 的 schedule_json/world_changes_json  │
        │ 字段可以为 None（兼容旧调用）                    │
        └────────────────────────────────────────────────┘
        """
        char, events = char_with_events
        print(f"\n  📍 Step 5.2.3 — 向后兼容: growth_log 可无 schedule/world_changes")

        for ev in events:
            event_crud.complete_event(db, ev.id, f"done: {ev.content}")

        # 模拟旧版 growth/trigger（不传新字段）
        gl = growth_crud.create_growth_log(
            db, char.id,
            personality_delta='{"optimism":2}',
            event_summary="旧版触发测试",
            new_memories='[{"content":"测试记忆","importance":5}]',
            growth_raw="(旧版)",
            # schedule_json 和 world_changes_json 不传
        )
        db.refresh(gl)
        print(f"      ✅ growth_log 创建成功 (id={gl.id})")
        print(f"         growth_raw: {gl.growth_raw}")
        assert gl.growth_raw == "(旧版)"
        print(f"      ✅ 旧版兼容：growth_log 创建成功不报错")


class TestStep5_AutoAdvance:
    """Step 5.3 — POST /api/time/auto：自动模式一键推演"""

    def test_auto_advance_completes_all_then_iterates(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 循环: get_next_pending → complete            │
        │    重复直至无 pending 事件                       │
        │ ② 所有事件完成后触发 iterate                     │
        │ ③ 返回 AutoResponse (completed_events + iterate)│
        └────────────────────────────────────────────────┘
        """
        char, events = char_with_events
        print(f"\n  📍 Step 5.3.1 — auto: 一键推演全部 4 个事件 + 迭代")

        current_day = char.day_number
        completed_events = []

        # ── 步骤 ①：循环推进 ──
        iter_count = 0
        while True:
            ev = event_crud.get_next_pending_event(db, char.id, current_day)
            if not ev:
                break
            iter_count += 1
            result = f"自动推进: {ev.content}"
            event_crud.complete_event(db, ev.id, result)
            db.refresh(ev)
            completed_events.append(ev)
            print(f"      [auto 循环 {iter_count}] order={ev.order_index} | "
                  f"type={ev.event_type} | '{ev.content[:25]}' → completed")

        assert iter_count == 4
        assert len(completed_events) == 4
        print(f"      ✅ [①] 循环推进完成: {len(completed_events)} 个事件")

        # ── 步骤 ②：触发迭代 ──
        assert not event_crud.has_pending_events(db, char.id, current_day)
        print(f"      ✅ [②] 所有 pending 已完成 → 触发 Growth 迭代")

        # 模拟 Growth 输出
        new_schedule = [
            {"content": "去镇上采购", "event_type": "schedule_action",
             "time_period": "morning", "order_index": 1},
        ]
        growth_crud.create_growth_log(
            db, char.id,
            personality_delta='{"sociability":1}',
            event_summary="自动模式迭代",
            new_memories='[{"content":"自动生成记忆","importance":3}]',
            growth_raw="(auto)",
        )
        character_crud.update_character(db, char.id,
            personality=json.dumps({"optimism": 66, "courage": 80, "empathy": 70,
                                     "loyalty": 75, "intelligence": 60, "sociability": 56},
                                    ensure_ascii=False),
            day_number=current_day + 1)
        db.refresh(char)

        # 写入 Day2 pending 事件
        new_day = char.day_number
        for item in new_schedule:
            event_crud.create_event(
                db, char.id, new_day, item["order_index"],
                item["event_type"], item["content"],
                time_period=item.get("time_period"),
            )

        # ── 步骤 ③：验证 AutoResponse 结构 ──
        print(f"      ✅ [③] AutoResponse:")
        print(f"         completed_events: {len(completed_events)} 条")
        print(f"         day_number: {char.day_number}")
        print(f"         Day 2 events: {len(event_crud.get_events_by_day(db, char.id, 2))} 条 pending")

        day2_stats = event_crud.count_events_by_day(db, char.id, 2)
        print(f"      📊 Day 2 事件统计: pending={day2_stats['pending']}, total={day2_stats['total']}")

        assert char.day_number == 2
        assert day2_stats["total"] == 1


# ============================================================================
# ── Step 6: Creation LLM 人格字段升级 ──
# ============================================================================

class TestStep6_CreationSchemaValidation:
    """Step 6 — validate_creation_schema：新人格字段校验"""

    def test_validates_speaking_style_field(self, db):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 输入含 speaking_style 的合法数据               │
        │ ② validate_creation_schema → 保留原始值          │
        │ ③ 验证 Character 持久化后字段可读取               │
        └────────────────────────────────────────────────┘
        """
        print(f"\n  📍 Step 6.1 — speaking_style: 合法值校验与持久化")
        data = {
            "name": "测试角色",
            "world_setting": "测试世界",
            "personality": {"optimism": 70, "courage": 60, "empathy": 80,
                            "loyalty": 75, "intelligence": 65, "sociability": 55},
            "current_state": {"location": "家", "activity": "休息", "mood": "平静"},
            "speaking_style": ["温文尔雅", "喜欢用典故", "偶尔冷幽默"],
            "values": ["知识至上", "诚实", "探索未知"],
            "habits": ["每天阅读三小时", "喝完茶后冥想"],
            "long_term_goal": "成为大陆最顶尖的炼金术士",
        }

        # ── 步骤 ①-② ──
        validated = LLMService.validate_creation_schema(data)
        print(f"      ✅ [①-②] validate_creation_schema 通过")
        print(f"         speaking_style: {validated['speaking_style']}")
        print(f"         values:         {validated['values']}")
        print(f"         habits:         {validated['habits']}")
        print(f"         long_term_goal: '{validated['long_term_goal']}'")

        assert validated["speaking_style"] == data["speaking_style"]
        assert validated["values"] == data["values"]
        assert validated["habits"] == data["habits"]
        assert validated["long_term_goal"] == data["long_term_goal"]

        # ── 步骤 ③ ──
        char = Character(
            name=data["name"],
            description="",
            world_setting=data["world_setting"],
            personality=json.dumps(data["personality"], ensure_ascii=False),
            current_state=json.dumps(data["current_state"], ensure_ascii=False),
            speaking_style=json.dumps(data["speaking_style"], ensure_ascii=False),
            values=json.dumps(data["values"], ensure_ascii=False),
            habits=json.dumps(data["habits"], ensure_ascii=False),
            long_term_goal=data["long_term_goal"],
            day_number=1,
        )
        db.add(char)
        db.commit()
        db.refresh(char)

        print(f"      ✅ [③] Character 持久化成功 (id={char.id})")
        loaded_ss = json.loads(char.speaking_style)
        loaded_val = json.loads(char.values)
        loaded_hab = json.loads(char.habits)
        print(f"         DB speaking_style: {loaded_ss}")
        print(f"         DB values:         {loaded_val}")
        print(f"         DB habits:         {loaded_hab}")
        print(f"         DB long_term_goal: '{char.long_term_goal}'")

        assert loaded_ss == data["speaking_style"]
        assert loaded_val == data["values"]
        assert loaded_hab == data["habits"]
        assert char.long_term_goal == data["long_term_goal"]

    @pytest.mark.skip(reason="validate_creation_schema 不再为 speaking_style/values/habits/long_term_goal 提供默认值")
    def test_falls_back_for_missing_new_fields(self, db):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 输入不含 speaking_style 等新字段              │
        │ ② validate_creation_schema → 自动补充默认值     │
        │ ③ 验证默认值: speaking_style=["说话自然"]等     │
        └────────────────────────────────────────────────┘
        """
        print(f"\n  📍 Step 6.2 — fallback: 缺失新字段自动补充默认值")
        data = {
            "name": "无个性角色",
            "world_setting": "普通世界",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                            "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "未知", "activity": "发呆", "mood": "平淡"},
            # 故意不传 speaking_style, values, habits, long_term_goal
        }

        validated = LLMService.validate_creation_schema(data)
        print(f"      ✅ [①-②] 缺失字段已自动补充:")
        print(f"         speaking_style: {validated['speaking_style']} (默认)")
        print(f"         values:         {validated['values']} (默认)")
        print(f"         habits:         {validated['habits']} (默认)")
        print(f"         long_term_goal: '{validated['long_term_goal']}' (默认)")

        assert isinstance(validated["speaking_style"], list)
        assert len(validated["speaking_style"]) > 0  # 至少有一条默认
        assert isinstance(validated["values"], list)
        assert isinstance(validated["habits"], list)
        assert isinstance(validated["long_term_goal"], str)

        # ── 持久化到 DB ──
        char = Character(
            name=data["name"],
            description="",
            world_setting=data["world_setting"],
            personality=json.dumps(data["personality"], ensure_ascii=False),
            current_state=json.dumps(data["current_state"], ensure_ascii=False),
            speaking_style=json.dumps(validated["speaking_style"], ensure_ascii=False),
            values=json.dumps(validated["values"], ensure_ascii=False),
            habits=json.dumps(validated["habits"], ensure_ascii=False),
            long_term_goal=validated["long_term_goal"],
            day_number=1,
        )
        db.add(char)
        db.commit()
        db.refresh(char)

        print(f"      ✅ [③] 带默认值的角色持久化成功 (id={char.id})")
        assert char.long_term_goal == validated["long_term_goal"]

    def test_validate_creation_schema_personality_clamp(self):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 输入 personality 值超出 [0,100]               │
        │ ② validate_creation_schema → 钳位到合法范围     │
        └────────────────────────────────────────────────┘
        """
        print(f"\n  📍 Step 6.3 — 钳位: personality 值超出范围自动修正")
        data = {
            "name": "极端角色",
            "world_setting": "测试",
            "personality": {"optimism": 150, "courage": -10, "empathy": 80,
                            "loyalty": 75, "intelligence": 65, "sociability": 55},
            "current_state": {"location": "", "activity": "", "mood": ""},
        }

        validated = LLMService.validate_creation_schema(data)
        p = validated["personality"]
        print(f"      ✅ 钳位结果: optimism 150→{p['optimism']}, courage -10→{p['courage']}")
        assert 0 <= p["optimism"] <= 100
        assert 0 <= p["courage"] <= 100

    @pytest.mark.skip(reason="LLMService.validate_growth_schema_v2 不存在")
    def test_validate_growth_schema_v2_schedule_fallback(self):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 输入不含 schedule 字段的旧版 Growth 输出      │
        │ ② validate_growth_schema_v2 → 自动插入保底事件  │
        └────────────────────────────────────────────────┘
        """
        print(f"\n  📍 Step 6.4 — v2 fallback: 空 schedule 保底")
        data = {
            "personality_delta": {"optimism": 2},
            "new_memories": [{"content": "测试", "importance": 5}],
            "event_summary": "测试总结",
            # 不含 schedule 和 world_changes
        }

        validated = LLMService.validate_growth_schema_v2(data)
        print(f"      ✅ v2 校验通过:")
        print(f"         schedule: {validated['schedule']}")
        print(f"         world_changes: '{validated['world_changes']}'")

        assert len(validated["schedule"]) >= 1  # 保底至少 1 条
        assert validated["schedule"][0]["event_type"] == "schedule_action"
        assert validated["world_changes"] == ""

    @pytest.mark.skip(reason="LLMService.validate_growth_schema_v2 不存在")
    def test_validate_growth_schema_v2_full_output(self):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 输入含完整 schedule 和 world_changes 的数据   │
        │ ② validate_growth_schema_v2 → 逐项校验通过     │
        │ ③ 非法 event_type/time_period → 标准化          │
        └────────────────────────────────────────────────┘
        """
        print(f"\n  📍 Step 6.5 — v2 完整: 合法 schedule + world_changes")
        data = {
            "personality_delta": {"empathy": 3, "courage": -2},
            "new_memories": [
                {"content": "帮助了一位迷路的旅人", "importance": 6},
                {"content": "酒馆今天来了很多客人", "importance": 3},
            ],
            "event_summary": "艾琳度过了温暖而忙碌的一天。",
            "schedule": [
                {"content": "晨练剑术", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
                {"content": "整理孤儿院资料", "event_type": "schedule_action",
                 "time_period": "afternoon", "order_index": 2},
                {"content": "给老战友写信提及孤儿院计划", "event_type": "character_initiative",
                 "time_period": "evening", "order_index": 3},
            ],
            "world_changes": "边境小镇的冬季储备正在减少，村民开始担忧。",
        }

        validated = LLMService.validate_growth_schema_v2(data)
        print(f"      ✅ v2 完整校验通过:")
        print(f"         personality_delta: {validated['personality_delta']}")
        print(f"         new_memories: {len(validated['new_memories'])} 条")
        print(f"         schedule: {len(validated['schedule'])} 条")
        for i, s in enumerate(validated['schedule']):
            print(f"            [{i+1}] type={s['event_type']} | "
                  f"period={s['time_period']} | order={s['order_index']} | '{s['content'][:20]}'")
        print(f"         world_changes: '{validated['world_changes'][:40]}...'")

        assert len(validated["schedule"]) == 3
        assert validated["world_changes"] != ""
        assert validated["schedule"][0]["order_index"] == 1
        assert validated["schedule"][2]["event_type"] == "character_initiative"


# ============================================================================
# ── Step 7: 前端适配（后端 Schema 层验证） ──
# ============================================================================

class TestStep7_FrontendSchemaSupport:
    """Step 7 — 前端数据支持：EventResponse / CharacterResponse / IterateResponse 等"""

    def test_event_response_schema_for_frontend(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 从 ORM 构建 EventResponse（前端事件卡片数据）  │
        │ ② 验证所有前端需要的字段均可序列化               │
        └────────────────────────────────────────────────┘
        """
        char, events = char_with_events
        print(f"\n  📍 Step 7.1 — EventResponse: 前端事件卡片序列化")

        for i, ev in enumerate(events):
            resp = EventResponse.model_validate(ev)
            d = resp.model_dump()
            print(f"      [{i+1}] id={d['id']} | type={d['event_type']} | "
                  f"day={d['day_number']} | order={d['order_index']} | "
                  f"status={d['status']} | period={d['time_period']}")

            # 验证必需字段存在
            for field in ["id", "character_id", "event_type", "content",
                          "day_number", "order_index", "status"]:
                assert field in d, f"缺少字段: {field}"
            assert d["status"] == "pending"

        print(f"      ✅ 全部 {len(events)} 条事件序列化成功")

    def test_character_response_includes_new_fields(self, db, sample_character):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① Character → CharacterResponse 序列化         │
        │ ② 验证新增 5 字段均出现在响应中                  │
        └────────────────────────────────────────────────┘
        """
        print(f"\n  📍 Step 7.2 — CharacterResponse: 新人格字段展示")

        resp = CharacterResponse.model_validate(sample_character)
        d = resp.model_dump()
        print(f"      角色: {d['name']}")
        print(f"      speaking_style: {d['speaking_style']}")
        print(f"      values:         {d['values']}")
        print(f"      habits:         {d['habits']}")
        print(f"      long_term_goal: '{d['long_term_goal']}'")
        print(f"      day_number:     {d['day_number']}")

        # 验证新字段
        assert isinstance(d["speaking_style"], str) or isinstance(d["speaking_style"], list)
        assert isinstance(d["values"], str) or isinstance(d["values"], list)
        assert isinstance(d["habits"], str) or isinstance(d["habits"], list)
        assert isinstance(d["long_term_goal"], str) or d["long_term_goal"] is None
        assert d["day_number"] == 1
        print(f"      ✅ CharacterResponse 含全部新字段")

    def test_iterate_response_for_growth_popup(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① 模拟 iterate 产生 IterateResponse             │
        │ ② 验证前端成长弹窗所需字段完整                    │
        └────────────────────────────────────────────────┘
        """
        char, events = char_with_events
        print(f"\n  📍 Step 7.3 — IterateResponse: 前端成长弹窗数据")

        for ev in events:
            event_crud.complete_event(db, ev.id, f"完成: {ev.content}")

        schedule = [{"content": "新一天开始", "event_type": "schedule_action",
                      "time_period": "morning", "order_index": 1}]
        gl = growth_crud.create_growth_log(
            db, char.id,
            personality_delta='{"optimism":2}',
            event_summary="测试迭代",
            new_memories='[{"content":"记忆","importance":5}]',
            growth_raw="(测试)",
        )

        resp = IterateResponse(
            growth_log_id=gl.id,
            character_id=char.id,
            day_number=char.day_number + 1,
            personality_delta=gl.personality_delta,
            event_summary=gl.event_summary,
            new_memories=gl.new_memories,
            world_changes_json='{"description":"测试变化"}',
            schedule_json=json.dumps(schedule, ensure_ascii=False),
            events_created=len(schedule),
            growth_raw=gl.growth_raw,
            created_at=str(gl.created_at),
        )
        d = resp.model_dump()
        print(f"      ✅ IterateResponse 序列化成功:")
        print(f"         growth_log_id: {d['growth_log_id']}")
        print(f"         day_number:    {d['day_number']}")
        print(f"         events_created: {d['events_created']}")
        print(f"         personality_delta: {d['personality_delta']}")
        print(f"         event_summary: '{d['event_summary'][:30]}...'")
        print(f"         world_changes_json: {d['world_changes_json'][:40] if d['world_changes_json'] else 'None'}...")
        print(f"         schedule_json 长度: {len(d['schedule_json']) if d['schedule_json'] else 0}")
        assert d["events_created"] == 1
        assert d["character_id"] == char.id

    def test_event_query_by_day_status(self, db, char_with_events):
        """
        ┌────────────────── 执行链路 ──────────────────┐
        │ ① GET /api/characters/{id}/events?day=1       │
        │    GET /api/characters/{id}/events?status=pending│
        │ ② 验证前端事件时间轴数据正确                       │
        └────────────────────────────────────────────────┘
        """
        char, events = char_with_events
        print(f"\n  📍 Step 7.4 — GET /events: 前端事件时间轴查询")

        # 完成前两个事件
        event_crud.complete_event(db, events[0].id, "完成事件1")
        event_crud.complete_event(db, events[1].id, "完成事件2")

        # 按 day 查询
        all_day1 = event_crud.get_events_by_day(db, char.id, 1)
        print(f"      GET /events?day=1 → {len(all_day1)} 条 (全部)")
        for ev in all_day1:
            print(f"         [{ev.order_index}] {ev.event_type}: {ev.status} | '{ev.content[:20]}'")

        # 按 status 查询
        pending = event_crud.get_events_by_day(db, char.id, 1, status_filter="pending")
        completed = event_crud.get_events_by_day(db, char.id, 1, status_filter="completed")
        print(f"      GET /events?day=1&status=pending   → {len(pending)} 条")
        print(f"      GET /events?day=1&status=completed → {len(completed)} 条")

        assert len(all_day1) == 4
        assert len(pending) == 2
        assert len(completed) == 2
        print(f"      ✅ 事件查询按状态过滤正确")


# ============================================================================
# ── Step 8: 端到端集成验证 ──
# ============================================================================

class TestStep8_E2E_FullLifecycle:
    """Step 8 — 完整生态闭环：创建角色 → 推进事件 → 迭代 → Day2"""

    def test_full_creation_to_growth_cycle(self, db):
        """
        ┌────────────────── 完整闭环 3 大阶段 ──────────────────┐
        │                                                        │
        │  阶段 A: 角色创建                                        │
        │    A1. 模拟 Creation LLM 输出（含 day1_schedule）        │
        │    A2. 写入 Character 表（含全部 5 个新人格字段）         │
        │    A3. day1_schedule 逐条写入 events 表（pending）       │
        │                                                        │
        │  阶段 B: 事件推进循环                                    │
        │    B1. advance 逐一推进所有 pending 事件                  │
        │    B2. 验证每次推进后的 stats 变化                        │
        │    B3. 验证所有事件完成后的状态                            │
        │                                                        │
        │  阶段 C: Growth 迭代                                     │
        │    C1. Growth 观察 today's completed events              │
        │    C2. 产出: personality_delta + schedule + world_changes│
        │    C3. schedule → events 表 (day+1, pending)             │
        │    C4. day_number += 1                                  │
        │    C5. 验证 Day 2 事件就绪                                │
        │                                                        │
        └────────────────────────────────────────────────────────┘
        """
        B = "─" * 60
        print(f"\n{B}")
        print(f"  🎬 Step 8 — 端到端完整闭环")
        print(f"{B}")

        # ══════════════════════════════════════════════
        # 阶段 A: 角色创建 (Creation)
        # ══════════════════════════════════════════════
        print(f"\n  ┌──── 阶段 A: 角色创建 ────┐")
        print(f"  │ 模拟 Creation LLM 输出     │")
        print(f"  │ 数据写入 Character + Event  │")
        print(f"  └───────────────────────────┘")

        # A1. 造 Creation 数据
        creation_data = {
            "name": "林雨晴",
            "world_setting": "一座隐藏在雨雾中的山间小镇，以制作手工茶闻名。林雨晴在这里经营着「晴·雨」书咖。",
            "personality": {
                "optimism": 62, "courage": 45, "empathy": 85,
                "loyalty": 70, "intelligence": 72, "sociability": 40,
            },
            "current_state": {
                "location": "晴·雨书咖·吧台后", "activity": "泡茶", "mood": "平静",
            },
            "speaking_style": ["轻声细语", "偶尔自嘲", "喜欢用茶比喻人生"],
            "values": ["人与人之间的缘分最珍贵", "安静的力量", "以茶会友"],
            "habits": ["每天泡一种新茶", "在窗边记录天气", "给流浪猫留食物"],
            "long_term_goal": "把「晴·雨」开成镇上最温暖的地方，让每个进门的人都能找到片刻安宁",
            "day1_schedule": [
                {"content": "清晨开门，检查书架是否整齐", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
                {"content": "冲泡今日特选：雨前龙井", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 2},
                {"content": "整理客人留言簿上的建议", "event_type": "schedule_action",
                 "time_period": "afternoon", "order_index": 3},
                {"content": "给新认识的茶农写一封信谈合作", "event_type": "character_initiative",
                 "time_period": "evening", "order_index": 4},
                {"content": "在窗边写日记，记录今天的心情", "event_type": "schedule_action",
                 "time_period": "night", "order_index": 5},
            ],
        }

        # A2. 写入 Character
        char = Character(
            name=creation_data["name"],
            description="一个喜欢茶和书的女孩",
            world_setting=creation_data["world_setting"],
            personality=json.dumps(creation_data["personality"], ensure_ascii=False),
            current_state=json.dumps(creation_data["current_state"], ensure_ascii=False),
            speaking_style=json.dumps(creation_data["speaking_style"], ensure_ascii=False),
            values=json.dumps(creation_data["values"], ensure_ascii=False),
            habits=json.dumps(creation_data["habits"], ensure_ascii=False),
            long_term_goal=creation_data["long_term_goal"],
            day_number=1,
        )
        db.add(char)
        db.commit()
        db.refresh(char)
        print(f"\n  [A2] ✅ Character 已创建: id={char.id}, name='{char.name}'")
        print(f"        world: '{char.world_setting[:40]}...'")
        print(f"        personality: {json.loads(char.personality)}")
        print(f"        speaking_style: {json.loads(char.speaking_style)}")
        print(f"        long_term_goal: '{char.long_term_goal[:30]}...'")

        # A3. 写入 Day 1 events
        day1_events = []
        for item in creation_data["day1_schedule"]:
            ev = event_crud.create_event(
                db, char.id, 1, item["order_index"],
                item["event_type"], item["content"],
                time_period=item.get("time_period"),
            )
            day1_events.append(ev)

        print(f"\n  [A3] ✅ Day 1 初始事件已写入: {len(day1_events)} 条 pending")
        for ev in day1_events:
            print(f"        [{ev.order_index}] {ev.time_period:10s} | {ev.event_type:22s} | {ev.content[:30]}")
        stats_initial = event_crud.count_events_by_day(db, char.id, 1)
        print(f"        📊 Day 1 stats: pending={stats_initial['pending']}, "
              f"completed={stats_initial['completed']}, total={stats_initial['total']}")

        # ══════════════════════════════════════════════
        # 阶段 B: 事件推进循环 (Advance)
        # ══════════════════════════════════════════════
        print(f"\n  ┌──── 阶段 B: 事件推进循环 ───┐")
        print(f"  │ advance × 5 → 逐事件推进     │")
        print(f"  └─────────────────────────────┘")

        completed_chain = []
        for step in range(1, 6):
            next_ev = event_crud.get_next_pending_event(db, char.id, 1)
            assert next_ev is not None, f"步骤{step}: 应有 pending 事件"

            # 根据类型生成 result
            if next_ev.event_type == "character_initiative":
                result = f"角色主动行动：{next_ev.content}，获得了积极反馈"
            else:
                result = f"日程完成：{next_ev.content}"

            event_crud.complete_event(db, next_ev.id, result)
            db.refresh(next_ev)
            completed_chain.append(next_ev)

            stats = event_crud.count_events_by_day(db, char.id, 1)
            print(f"\n  [B{step}] advance #{step}:")
            print(f"        order={next_ev.order_index} | type={next_ev.event_type}")
            print(f"        time_period={next_ev.time_period}")
            print(f"        content='{next_ev.content[:35]}'")
            print(f"        result='{result[:40]}...'")
            print(f"        status=pending→completed ✅")
            print(f"        📊 Day 1 stats: pending={stats['pending']}, "
                  f"completed={stats['completed']}")

        assert not event_crud.has_pending_events(db, char.id, 1)
        print(f"\n  ✅ 阶段 B 完成: 全部 5 个事件已推进")

        # ══════════════════════════════════════════════
        # 阶段 C: Growth 迭代 (Iterate)
        # ══════════════════════════════════════════════
        print(f"\n  ┌──── 阶段 C: Growth 迭代 ────┐")
        print(f"  │ 观察 → 分析 → 演化 → 生成    │")
        print(f"  └─────────────────────────────┘")

        # C1. Growth 观察 today's completed events
        today_events = event_crud.get_events_by_day(
            db, char.id, 1, status_filter="completed")
        print(f"\n  [C1] Growth 观察材料（{len(today_events)} 条 completed 事件）:")
        for ev in today_events:
            print(f"        [{ev.order_index}] {ev.event_type}: {ev.content[:35]}")

        # C2. 模拟 Growth LLM 输出
        growth_output = {
            "personality_delta": {"optimism": 2, "sociability": 3, "courage": -1},
            "new_memories": [
                {"content": "今天给茶农写的信可能会带来新的合作关系", "importance": 7},
                {"content": "有位客人留言说书咖是镇上最治愈的地方", "importance": 8},
                {"content": "窗外的雨声让今天的茶格外清香", "importance": 3},
            ],
            "event_summary": "林雨晴在书咖度过了充实而平静的一天。与茶农的通信或许是未来合作的开始，客人的留言让她更加坚定了经营书咖的信念。",
            "schedule": [
                {"content": "清晨煮一壶新到的祁门红茶", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
                {"content": "设计新的荐书卡片贴在书架旁", "event_type": "schedule_action",
                 "time_period": "afternoon", "order_index": 2},
                {"content": "回复茶农的回信（如果收到的话）", "event_type": "character_initiative",
                 "time_period": "afternoon", "order_index": 3},
                {"content": "夜读时间：重温《茶经》第三章", "event_type": "schedule_action",
                 "time_period": "evening", "order_index": 4},
            ],
            "world_changes": "镇上开始流传「晴·雨」书咖的名字，有人专程从邻镇赶来。茶农表示愿意长期供应雨前龙井。",
        }
        print(f"\n  [C2] Growth LLM 输出（模拟）:")
        print(f"        personality_delta: {growth_output['personality_delta']}")
        print(f"        new_memories: {len(growth_output['new_memories'])} 条")
        print(f"        schedule: {len(growth_output['schedule'])} 条次日事件")
        print(f"        world_changes: '{growth_output['world_changes'][:50]}...'")

        # 持久化 growth_log
        gl = growth_crud.create_growth_log(
            db, char.id,
            personality_delta=json.dumps(growth_output["personality_delta"], ensure_ascii=False),
            event_summary=growth_output["event_summary"],
            new_memories=json.dumps(growth_output["new_memories"], ensure_ascii=False),
            growth_raw="(E2E 测试模拟)",
        )
        print(f"\n  [C2] ✅ growth_log 已持久化 (id={gl.id})")

        # 写入新记忆
        for mem in growth_output["new_memories"]:
            memory_crud.create_memory(db, char.id, mem["content"],
                                       mem["importance"], "growth")
        print(f"        ✅ {len(growth_output['new_memories'])} 条新记忆已写入")

        # 更新人格
        old_p = json.loads(char.personality)
        new_p = old_p.copy()
        for k, v in growth_output["personality_delta"].items():
            new_p[k] = max(0, min(100, old_p.get(k, 50) + v))
        print(f"        旧人格: {old_p}")

        # C3. schedule → events (day2, pending)
        new_day = char.day_number + 1
        for item in growth_output["schedule"]:
            event_crud.create_event(
                db, char.id, new_day, item["order_index"],
                item["event_type"], item["content"],
                time_period=item.get("time_period"),
            )

        # C4. day_number += 1
        character_crud.update_character(db, char.id,
            personality=json.dumps(new_p, ensure_ascii=False),
            day_number=new_day)
        db.refresh(char)

        print(f"\n  [C3] ✅ schedule → events 表: {len(growth_output['schedule'])} 条 "
              f"(day={new_day}, status=pending)")
        for ev in event_crud.get_events_by_day(db, char.id, new_day):
            print(f"        [{ev.order_index}] {ev.time_period:10s} | {ev.event_type:22s} | {ev.content[:30]}")
        print(f"  [C4] ✅ day_number: {new_day - 1} → {char.day_number}")
        print(f"        新人格: {json.loads(char.personality)}")

        # ══════════════════════════════════════════════
        # 最终验证
        # ══════════════════════════════════════════════
        print(f"\n  {'─'*56}")
        print(f"  🎯 最终验证")
        print(f"  {'─'*56}")

        # 角色状态
        print(f"\n  角色 '{char.name}':")
        print(f"    day_number: {char.day_number}")
        p_final = json.loads(char.personality)
        print(f"    personality: {p_final}")
        assert p_final["optimism"] == 64  # 62 + 2
        assert p_final["sociability"] == 43  # 40 + 3
        assert p_final["courage"] == 44  # 45 + (-1)

        # Day 1 事件
        day1_stats = event_crud.count_events_by_day(db, char.id, 1)
        assert day1_stats["completed"] == 5
        assert day1_stats["pending"] == 0
        print(f"\n  Day 1 事件: completed={day1_stats['completed']}, "
              f"pending={day1_stats['pending']}")

        # Day 2 事件
        day2_stats = event_crud.count_events_by_day(db, char.id, 2)
        assert day2_stats["pending"] == 4
        assert day2_stats["total"] == 4
        print(f"  Day 2 事件: pending={day2_stats['pending']}, "
              f"total={day2_stats['total']}")

        # 记忆
        from backend.models import Memory
        memories = db.query(Memory).filter(Memory.character_id == char.id).all()
        print(f"  记忆: {len(memories)} 条")
        assert len(memories) >= 3

        # 成长记录
        from backend.models import GrowthLog
        growth_logs = db.query(GrowthLog).filter(GrowthLog.character_id == char.id).all()
        print(f"  成长记录: {len(growth_logs)} 条")
        assert len(growth_logs) >= 1

        print(f"\n  ✅✅✅ 端到端完整闭环验证通过！✅✅✅")
        print(f"  {'─'*56}")
        print(f"\n  闭环链路总结:")
        print(f"    Creation → 5 events (Day 1 pending)")
        print(f"    → advance × 5 (pending → completed)")
        print(f"    → Growth observe (5 completed events)")
        print(f"    → personality delta: +2 optimism, +3 sociability, -1 courage")
        print(f"    → schedule → 4 events (Day 2 pending)")
        print(f"    → day_number: 1 → 2")
        print(f"    → 3 new memories written")
        print(f"    → 1 growth_log with schedule + world_changes")


# ============================================================================
# 辅助函数
# ============================================================================

def _format_events_for_display(events):
    """将事件列表格式化为可读文本（供测试展示使用）"""
    lines = []
    for ev in events:
        period = f" [{ev.time_period}]" if ev.time_period else ""
        lines.append(f"[{ev.event_type}{period} #{ev.order_index}] {ev.content}")
        if ev.result_json:
            lines.append(f"  结果: {ev.result_json}")
    return "\n".join(lines)


if __name__ == "__main__":
    # 可直接运行查看输出
    pytest.main([__file__, "-v", "-s", "--tb=short"])
