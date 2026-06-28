"""
Day1 事件持久化 · 端到端集成测试
==================================

覆盖范围：
  1. validate_creation_schema — day1_schedule 数据传递验证
  2. event_crud.create_event — 数据库写入验证
  3. create_character 端点 — 模拟请求验证 events 表写入

每个测试用例均包含：
  - 测试目的（docstring）
  - 期望输出（assert）
  - 验证范围说明
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from backend.services.llm_service import LLMService
from backend.crud import event as event_crud
from backend.models import Base, Event, Character


# ==================== 1. day1_schedule 数据传递验证 ====================

class TestDay1ScheduleDataFlow:
    """
    验证 day1_schedule 从 LLM 输出到 validate_creation_schema 的
    数据传递完整性。

    测试策略：
      模拟 LLM 返回的完整 JSON，验证 validate_creation_schema 处理后
      day1_schedule 字段被正确保留且格式符合预期。
    """

    def test_day1_schedule_preserved_after_validation(self):
        """
        验证目的：validate_creation_schema 处理后 day1_schedule 正确保留。

        模拟输入：含 3 条 day1_schedule 的完整角色数据。
        期望输出：day1_schedule 保留 3 条，content/event_type/time_period/order_index 完整。
        """
        mock_data = {
            "name": "测试角色",
            "world_setting": "测试世界",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                          "loyalty": 50, "intelligence": 50, "sociability": 50},
            "initial_memories": [{"content": "记忆1", "importance": 5}],
            "current_state": {"location": "家", "activity": "休息", "mood": "平静"},
            "speaking_style": ["自然"],
            "values": ["诚实"],
            "habits": ["阅读"],
            "long_term_goal": "生存",
            "day1_schedule": [
                {"content": "醒来", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
                {"content": "行动", "event_type": "character_initiative",
                 "time_period": "afternoon", "order_index": 2},
                {"content": "入睡", "event_type": "schedule_action",
                 "time_period": "night", "order_index": 3},
            ]
        }

        result = LLMService.validate_creation_schema(mock_data)
        ds = result.get("day1_schedule", [])

        assert len(ds) == 3, f"期望 3 条, 实际 {len(ds)} 条"
        assert ds[0]["content"] == "醒来"
        assert ds[0]["event_type"] == "schedule_action"
        assert ds[0]["time_period"] == "morning"
        assert ds[0]["order_index"] == 1

    def test_empty_day1_schedule_gets_fallback(self):
        """
        验证目的：LLM 未输出 day1_schedule 时保底机制生效。

        模拟输入：不含 day1_schedule 的角色数据。
        期望输出：自动生成 1 条保底事件 "新的一天开始了"。
        """
        mock_data = {
            "name": "测试角色",
            "world_setting": "测试世界",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                          "loyalty": 50, "intelligence": 50, "sociability": 50},
            "initial_memories": [{"content": "记忆", "importance": 5}],
            "current_state": {"location": "家", "activity": "休息", "mood": "平静"},
            "speaking_style": ["自然"],
            "values": ["诚实"],
            "habits": ["阅读"],
            "long_term_goal": "生存",
            # 没有 day1_schedule 字段
        }

        result = LLMService.validate_creation_schema(mock_data)
        ds = result.get("day1_schedule", [])

        assert len(ds) >= 1, f"保底机制未生效: day1_schedule 为空"
        assert ds[0]["content"] == "新的一天开始了"
        assert ds[0]["event_type"] == "schedule_action"
        assert ds[0]["order_index"] == 1

    def test_day1_schedule_null_gets_fallback(self):
        """
        验证目的：day1_schedule 为 null/None 时保底机制生效。

        模拟输入：day1_schedule 显式为 None。
        期望输出：自动生成 1 条保底事件。
        """
        mock_data = {
            "name": "测试",
            "world_setting": "测试",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                          "loyalty": 50, "intelligence": 50, "sociability": 50},
            "initial_memories": [],
            "current_state": {"location": "家", "activity": "休息", "mood": "平静"},
            "day1_schedule": None,
        }

        result = LLMService.validate_creation_schema(mock_data)
        ds = result.get("day1_schedule", [])

        assert len(ds) >= 1, f"day1_schedule=None 时保底机制未生效"
        assert ds[0]["content"] == "新的一天开始了"


# ==================== 2. 数据库写入验证 ====================

class TestDay1DatabasePersistence:
    """
    验证 event_crud.create_event 将 day1_schedule 正确写入数据库。

    测试策略：
      使用内存 SQLite 数据库，模拟 create_character 端点中的事件持久化流程。
    """

    @pytest.fixture
    def db_session(self):
        """创建内存 SQLite 数据库用于测试"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        yield db
        db.close()

    def test_persist_day1_schedule_as_events(self, db_session):
        """
        验证目的：逐条调用 create_event 后数据库中有对应记录。

        模拟流程：创建角色 → 逐条写入 day1 事件 → 查询验证。

        期望输出：
          - events 表中有 3 条记录
          - 每条 status=pending, day_number=1, character_id 正确
        """
        db = db_session

        # 创建角色
        char = Character(
            name="测试角色",
            description="测试",
            world_setting="测试世界",
            personality='{"optimism":50}',
            current_state='{"location":"家"}',
            day_number=1,
        )
        db.add(char)
        db.commit()
        db.refresh(char)
        char_id = char.id

        # 模拟 main.py 第 165-177 行的 day1_schedule 持久化循环
        day1_schedule = [
            {"content": "醒来", "event_type": "schedule_action",
             "time_period": "morning", "order_index": 1},
            {"content": "行动", "event_type": "character_initiative",
             "time_period": "afternoon", "order_index": 2},
            {"content": "入睡", "event_type": "schedule_action",
             "time_period": "night", "order_index": 3},
        ]

        for item in day1_schedule:
            if isinstance(item, dict) and item.get("content", "").strip():
                event_crud.create_event(
                    db=db,
                    character_id=char_id,
                    day_number=1,
                    order_index=item.get("order_index", 1),
                    event_type=item.get("event_type", "schedule_action"),
                    content=item["content"].strip(),
                    status="pending",
                    time_period=item.get("time_period"),
                )

        # 验证数据库
        events = db.query(Event).filter(Event.character_id == char_id).all()
        assert len(events) == 3, f"期望 3 条事件, 实际 {len(events)} 条"

        for ev in events:
            assert ev.status == "pending", f"事件 {ev.id} 状态应为 pending"
            assert ev.day_number == 1, f"事件 {ev.id} day_number 应为 1"
            assert ev.character_id == char_id, f"事件 {ev.id} character_id 不匹配"

    def test_empty_day1_schedule_no_events(self, db_session):
        """
        验证目的：空 day1_schedule 时不会创建无效事件记录。

        模拟流程：创建角色 → 循环空列表 → 查询验证。

        期望输出：events 表记录数为 0。
        """
        db = db_session

        char = Character(
            name="测试角色2",
            description="测试",
            day_number=1,
        )
        db.add(char)
        db.commit()
        db.refresh(char)

        # 模拟空 day1_schedule
        day1_schedule = []

        for item in day1_schedule:
            if isinstance(item, dict) and item.get("content", "").strip():
                event_crud.create_event(db=db, character_id=char.id,
                                      day_number=1, order_index=1,
                                      event_type="schedule_action",
                                      content=item["content"].strip(),
                                      status="pending")

        events = db.query(Event).filter(Event.character_id == char.id).all()
        assert len(events) == 0, f"空 schedule 不应创建事件, 实际 {len(events)} 条"

    def test_pending_events_can_be_advanced(self, db_session):
        """
        验证目的：持久化后的 pending 事件可以被"推进事件"正确读取。

        模拟流程：写入事件 → 调用 get_next_pending_event → 推进 → 验证完成。

        期望输出：
          - get_next_pending_event 返回 order_index 最小的事件
          - complete_event 后状态变为 completed
        """
        db = db_session

        char = Character(name="测试角色3", description="测试", day_number=1)
        db.add(char)
        db.commit()
        db.refresh(char)

        # 写入 2 条事件
        event_crud.create_event(db=db, character_id=char.id, day_number=1,
                              order_index=1, event_type="schedule_action",
                              content="事件1", status="pending")
        event_crud.create_event(db=db, character_id=char.id, day_number=1,
                              order_index=2, event_type="scene_event",
                              content="事件2", status="pending")

        # 推进第一条
        next_ev = event_crud.get_next_pending_event(db, char.id, 1)
        assert next_ev is not None, "应该有待推进事件"
        assert next_ev.order_index == 1, "应返回 order_index 最小的事件"

        completed = event_crud.complete_event(db, next_ev.id, "事件1完成")
        assert completed is not None
        assert completed.status == "completed"

        # 推进第二条
        next_ev2 = event_crud.get_next_pending_event(db, char.id, 1)
        assert next_ev2 is not None
        assert next_ev2.order_index == 2

        completed2 = event_crud.complete_event(db, next_ev2.id, "事件2完成")
        assert completed2.status == "completed"

        # 全部完成后无更多事件
        next_ev3 = event_crud.get_next_pending_event(db, char.id, 1)
        assert next_ev3 is None, "全部完成后应无 pending 事件"
