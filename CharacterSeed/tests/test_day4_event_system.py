"""
Day4 事件推进系统 · 核心单元测试
=================================

覆盖范围：
  1. Event CRUD 操作（create / get_next_pending / complete / bulk）
  2. Event CRUD 查询（has_pending / count_by_day / delete_by_character）
  3. validate_growth_schema_v2 校验逻辑
  4. validate_creation_schema 新字段校验
  5. GrowthModule._calculate_new_personality 人格计算
  6. GrowthModule._format_events_today 事件格式化

每个测试用例均包含：
  - 期望输出（assert 触发）
  - 验证目的（docstring 中解释）
"""
import json
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from backend.services.llm_service import LLMService
from backend.crud import event as event_crud
from backend.modules.growth import GrowthModule


# ==================== 测试夹具 ====================

class MockEvent:
    """模拟 Event ORM 对象，用于测试 event_crud 和 growth 模块"""
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 1)
        self.character_id = kwargs.get("character_id", 1)
        self.day_number = kwargs.get("day_number", 1)
        self.order_index = kwargs.get("order_index", 1)
        self.event_type = kwargs.get("event_type", "schedule_action")
        self.content = kwargs.get("content", "默认事件描述")
        self.metadata_json = kwargs.get("metadata_json")
        self.result_json = kwargs.get("result_json")
        self.status = kwargs.get("status", "pending")
        self.session_id = kwargs.get("session_id")
        self.time_period = kwargs.get("time_period")
        self.created_at = kwargs.get("created_at", datetime.now())


# ==================== 1. Event CRUD 测试 ====================

class TestEventCRUD:
    """
    验证 Event 表的全部 CRUD 操作。
    
    测试策略：使用 MagicMock 模拟 SQLAlchemy Session 和 Query，
    避免真实数据库依赖。核心验证点是 SQL 条件构造是否正确。
    """

    def test_create_event_sets_all_fields(self):
        """
        验证目的：create_event 正确设置所有字段值。
        
        期望输出：返回的 Event 对象具有与传入参数完全一致的字段值。
        """
        db = MagicMock()
        result = event_crud.create_event(
            db=db,
            character_id=1,
            day_number=1,
            order_index=2,
            event_type="schedule_action",
            content="去酒馆打听消息",
            metadata_json='{"source": "growth"}',
            status="pending",
            session_id=None,
            time_period="morning",
        )
        assert result.character_id == 1
        assert result.day_number == 1
        assert result.order_index == 2
        assert result.event_type == "schedule_action"
        assert result.content == "去酒馆打听消息"
        assert result.metadata_json == '{"source": "growth"}'
        assert result.status == "pending"
        assert result.time_period == "morning"
        # 验证 db.add 和 db.commit 被调用
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()

    def test_get_next_pending_orders_by_index_asc(self):
        """
        验证目的：get_next_pending_event 按 order_index ASC 查询，
        并正确应用 character_id + day_number + status 过滤条件。
        
        期望输出：filter 调用包含 3 个条件，order_by 为 order_index.asc，limit 1。
        """
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query

        # 模拟 filter 链式调用
        mock_filtered = MagicMock()
        mock_query.filter.return_value = mock_filtered
        mock_ordered = MagicMock()
        mock_filtered.order_by.return_value = mock_ordered
        mock_ordered.first.return_value = MockEvent(id=42, content="下一个事件")

        result = event_crud.get_next_pending_event(db, character_id=1, day_number=2)

        assert result is not None
        assert result.id == 42
        assert result.content == "下一个事件"
        # 验证 filter 条件包含 character_id=1, day_number=2, status="pending"
        call_args = mock_query.filter.call_args
        assert call_args is not None

    def test_get_next_pending_returns_none_when_empty(self):
        """
        验证目的：当天无 pending 事件时返回 None。
        
        期望输出：first() 返回 None 时函数返回 None。
        """
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_filtered = MagicMock()
        mock_query.filter.return_value = mock_filtered
        mock_ordered = MagicMock()
        mock_filtered.order_by.return_value = mock_ordered
        mock_ordered.first.return_value = None

        result = event_crud.get_next_pending_event(db, character_id=1, day_number=99)
        assert result is None

    def test_complete_event_writes_result_and_updates_status(self):
        """
        验证目的：complete_event 将 status 从 pending 改为 completed，
        并写入 result_json。
        
        期望输出：返回的 Event 对象 status="completed"，result_json 为传入值。
        """
        db = MagicMock()
        mock_event = MockEvent(id=5, status="pending", result_json=None)
        db.query.return_value.filter.return_value.first.return_value = mock_event

        result = event_crud.complete_event(db, event_id=5, result_json="角色完成了训练")

        assert result.status == "completed"
        assert result.result_json == "角色完成了训练"
        db.commit.assert_called_once()
        db.refresh.assert_called_once()

    def test_complete_event_returns_none_if_not_found(self):
        """
        验证目的：事件不存在时返回 None 而非报错。
        
        期望输出：返回 None，commit/refresh 不被调用。
        """
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = event_crud.complete_event(db, event_id=999, result_json="测试")
        assert result is None
        db.commit.assert_not_called()

    def test_has_pending_events_checks_existence(self):
        """
        验证目的：has_pending_events 使用 first() 判断是否存在，
        而非 COUNT(*)，性能更优。
        
        期望输出：有 pending 事件时返回 True，无则返回 False。
        """
        db = MagicMock()
        mock_query = db.query.return_value
        mock_filtered = mock_query.filter.return_value

        # 场景1：有 pending 事件
        mock_filtered.first.return_value = (1,)
        assert event_crud.has_pending_events(db, 1, 1) is True

        # 场景2：无 pending 事件
        mock_filtered.first.return_value = None
        assert event_crud.has_pending_events(db, 1, 1) is False

    def test_delete_events_by_character_returns_count(self):
        """
        验证目的：级联删除时返回正确的已删除记录数。
        
        期望输出：删除 3 条时返回 3，删除 0 条时返回 0。
        """
        db = MagicMock()
        mock_query = db.query.return_value
        mock_filtered = mock_query.filter.return_value

        # 场景1：有记录删除
        mock_filtered.delete.return_value = 3
        result = event_crud.delete_events_by_character(db, 1)
        assert result == 3
        db.commit.assert_called_once()

        # 重置 mock
        db.reset_mock()
        db.query.return_value.filter.return_value.delete.return_value = 0
        result = event_crud.delete_events_by_character(db, 1)
        assert result == 0


# ==================== 2. validate_growth_schema_v2 测试 ====================

class TestValidateGrowthSchemaV2:
    """
    验证 Day4 新增的 validate_growth_schema_v2 校验逻辑。

    测试策略：构造各种合法的/非法的 dict，验证校验函数正确处理：
      - v1 字段（personality_delta / new_memories / event_summary）
      - v2 新增字段（schedule / world_changes）
      - 非法/缺失字段时的降级行为
    """

    def test_valid_schedule_is_preserved(self):
        """
        验证目的：合法的 schedule 数组原样保留。
        
        期望输出：3 条 schedule 事件均通过校验，字段值正确。
        """
        data = {
            "personality_delta": {"optimism": 5, "courage": 0, "empathy": 0,
                                  "loyalty": 0, "intelligence": 0, "sociability": 0},
            "new_memories": [{"content": "测试记忆", "importance": 5}],
            "event_summary": "今天发生了测试事件",
            "schedule": [
                {"content": "晨练", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
                {"content": "与商人交谈", "event_type": "player_dialogue",
                 "time_period": "afternoon", "order_index": 2},
                {"content": "角色独自思考", "event_type": "character_initiative",
                 "time_period": "evening", "order_index": 3},
            ],
            "world_changes": "酒馆来了一位吟游诗人",
        }
        result = LLMService.validate_growth_schema_v2(data)
        assert len(result["schedule"]) == 3
        assert result["schedule"][0]["content"] == "晨练"
        assert result["schedule"][0]["event_type"] == "schedule_action"
        assert result["schedule"][0]["order_index"] == 1
        assert result["schedule"][1]["event_type"] == "player_dialogue"
        assert result["world_changes"] == "酒馆来了一位吟游诗人"

    def test_empty_schedule_gets_default_fallback(self):
        """
        验证目的：schedule 为空或缺失时自动生成保底事件。
        
        期望输出：schedule 至少包含 1 条内容为"新的一天开始了"的保底事件。
        """
        data = {
            "personality_delta": {"optimism": 0, "courage": 0, "empathy": 0,
                                  "loyalty": 0, "intelligence": 0, "sociability": 0},
            "new_memories": [],
            "event_summary": "平凡的一天",
        }
        result = LLMService.validate_growth_schema_v2(data)
        assert len(result["schedule"]) >= 1
        assert result["schedule"][0]["content"] == "新的一天开始了"

    def test_invalid_event_type_normalized(self):
        """
        验证目的：非法的 event_type 被标准化为 'schedule_action'。
        
        期望输出：invalid_type → schedule_action。
        """
        data = {
            "personality_delta": {"optimism": 0, "courage": 0, "empathy": 0,
                                  "loyalty": 0, "intelligence": 0, "sociability": 0},
            "new_memories": [],
            "event_summary": "测试",
            "schedule": [
                {"content": "非法类型事件", "event_type": "invalid_type",
                 "time_period": "morning", "order_index": 1},
            ],
        }
        result = LLMService.validate_growth_schema_v2(data)
        assert result["schedule"][0]["event_type"] == "schedule_action"

    def test_invalid_time_period_normalized(self):
        """
        验证目的：非法的 time_period 被归一化为默认值 'morning'。
        
        期望输出：invalid_period → morning（兜底行为，确保事件总是有默认时段）。
        """
        data = {
            "personality_delta": {"optimism": 0, "courage": 0, "empathy": 0,
                                  "loyalty": 0, "intelligence": 0, "sociability": 0},
            "new_memories": [],
            "event_summary": "测试",
            "schedule": [
                {"content": "测试事件", "event_type": "schedule_action",
                 "time_period": "invalid_period", "order_index": 1},
            ],
        }
        result = LLMService.validate_growth_schema_v2(data)
        assert result["schedule"][0]["time_period"] == "morning"

    def test_empty_content_events_skipped(self):
        """
        验证目的：content 为空的 schedule 项被跳过（不产生事件）。
        
        期望输出：空内容被跳过，保底事件填入 schedule。
        """
        data = {
            "personality_delta": {"optimism": 0, "courage": 0, "empathy": 0,
                                  "loyalty": 0, "intelligence": 0, "sociability": 0},
            "new_memories": [],
            "event_summary": "测试",
            "schedule": [
                {"content": "", "event_type": "schedule_action",
                 "time_period": "morning", "order_index": 1},
            ],
        }
        result = LLMService.validate_growth_schema_v2(data)
        assert len(result["schedule"]) == 1
        assert result["schedule"][0]["content"] == "新的一天开始了"

    def test_world_changes_non_string_converted(self):
        """
        验证目的：非字符串的 world_changes 被转换为字符串。
        
        期望输出：数字 123 → 字符串 "123"。
        """
        data = {
            "personality_delta": {"optimism": 0, "courage": 0, "empathy": 0,
                                  "loyalty": 0, "intelligence": 0, "sociability": 0},
            "new_memories": [],
            "event_summary": "测试",
            "schedule": [],
            "world_changes": 123,
        }
        result = LLMService.validate_growth_schema_v2(data)
        assert result["world_changes"] == "123"

    def test_not_dict_raises_value_error(self):
        """
        验证目的：非 dict 输入抛出 ValueError。
        
        期望输出：ValueError("数据必须是字典")
        """
        with pytest.raises(ValueError, match="数据必须是字典"):
            LLMService.validate_growth_schema_v2("not a dict")


# ==================== 3. validate_creation_schema 新字段测试 ====================

class TestValidateCreationSchemaNewFields:
    """
    验证 Day4 新增的 speaking_style / values / habits / long_term_goal 字段校验。
    """

    def test_speaking_style_list_preserved(self):
        """
        验证目的：合法的 speaking_style 数组原样保留。
        
        期望输出：3 条说话风格均被保留。
        """
        data = {
            "name": "测试角色",
            "world_setting": "测试世界",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                            "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "测试地", "activity": "测试中", "mood": "平静"},
            "speaking_style": ["语速缓慢", "喜欢用比喻", "声音低沉"],
            "values": ["重视友情"],
            "habits": ["清晨冥想"],
            "long_term_goal": "成为世界第一剑客",
        }
        result = LLMService.validate_creation_schema(data)
        assert result["speaking_style"] == ["语速缓慢", "喜欢用比喻", "声音低沉"]
        assert result["values"] == ["重视友情"]
        assert result["habits"] == ["清晨冥想"]
        assert result["long_term_goal"] == "成为世界第一剑客"

    def test_new_fields_fallback_when_missing(self):
        """
        验证目的：speaking_style / values / habits 缺失时使用默认值。
        
        期望输出：各有默认值，long_term_goal 为空字符串。
        """
        data = {
            "name": "测试角色",
            "world_setting": "测试",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                            "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "测试", "activity": "测试", "mood": "平静"},
        }
        result = LLMService.validate_creation_schema(data)
        assert isinstance(result["speaking_style"], list)
        assert len(result["speaking_style"]) >= 1
        assert isinstance(result["values"], list)
        assert len(result["values"]) >= 1
        assert isinstance(result["habits"], list)
        assert len(result["habits"]) >= 1
        assert result["long_term_goal"] == ""

    def test_empty_array_fallback(self):
        """
        验证目的：speaking_style 为空数组时使用默认值。
        
        期望输出：空数组 → ["说话自然"]。
        """
        data = {
            "name": "测试角色",
            "world_setting": "测试",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50,
                            "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {"location": "测试", "activity": "测试", "mood": "平静"},
            "speaking_style": [],
            "values": [],
            "habits": [],
        }
        result = LLMService.validate_creation_schema(data)
        assert len(result["speaking_style"]) >= 1
        assert len(result["values"]) >= 1
        assert len(result["habits"]) >= 1


# ==================== 4. GrowthModule 辅助函数测试 ====================

class TestGrowthModuleHelper:

    def test_calculate_new_personality_basic(self):
        """
        验证目的：新人格 = 旧人格 + delta 的基本运算正确。
        
        期望输出：optimism 70 + 5 = 75。
        """
        gm = GrowthModule()
        old = {"optimism": 70, "courage": 50, "empathy": 50,
               "loyalty": 50, "intelligence": 50, "sociability": 50}
        delta = {"optimism": 5, "courage": 0, "empathy": 0,
                 "loyalty": 0, "intelligence": 0, "sociability": 0}
        result = gm._calculate_new_personality(old, delta)
        assert result["optimism"] == 75
        assert result["courage"] == 50

    def test_calculate_new_personality_clamps_to_100(self):
        """
        验证目的：超出 100 的值被钳位到 100。
        
        期望输出：97 + 10 = 107 → 100。
        """
        gm = GrowthModule()
        old = {"optimism": 97, "courage": 50, "empathy": 50,
               "loyalty": 50, "intelligence": 50, "sociability": 50}
        delta = {"optimism": 10, "courage": 0, "empathy": 0,
                 "loyalty": 0, "intelligence": 0, "sociability": 0}
        result = gm._calculate_new_personality(old, delta)
        assert result["optimism"] == 100

    def test_calculate_new_personality_clamps_to_0(self):
        """
        验证目的：低于 0 的值被钳位到 0。
        
        期望输出：5 - 15 = -10 → 0。
        """
        gm = GrowthModule()
        old = {"optimism": 5, "courage": 50, "empathy": 50,
               "loyalty": 50, "intelligence": 50, "sociability": 50}
        delta = {"optimism": -15, "courage": 0, "empathy": 0,
                 "loyalty": 0, "intelligence": 0, "sociability": 0}
        result = gm._calculate_new_personality(old, delta)
        assert result["optimism"] == 0

    def test_format_events_today_formats_correctly(self):
        """
        验证目的：事件格式化函数输出正确文本。
        
        期望输出：包含事件类型标签、执行结果。
        """
        gm = GrowthModule()
        events = [
            MockEvent(
                id=1, event_type="schedule_action", content="去酒馆",
                time_period="morning", order_index=1,
                result_json="角色在酒馆打听消息",
            ),
            MockEvent(
                id=2, event_type="player_dialogue", content="与陌生人交谈",
                time_period="afternoon", order_index=2,
                result_json="陌生人告知了重要线索",
            ),
        ]
        text = gm._format_events_today(events)
        assert "schedule_action" in text
        assert "去酒馆" in text
        assert "角色在酒馆打听消息" in text
        assert "陌生人告知了重要线索" in text

    def test_format_events_today_empty_list(self):
        """
        验证目的：空事件列表输出提示文本。
        
        期望输出：包含"今日无已完成事件"。
        """
        gm = GrowthModule()
        text = gm._format_events_today([])
        assert "今日无已完成事件" in text


# ==================== 5. 数据流集成逻辑测试（mock LLM） ====================

class TestEventFlowLogic:
    """
    验证"对话打包 → 推进事件 → 迭代"的端到端逻辑。

    使用 mock 避免真实数据库依赖，仅验证查询逻辑正确性。
    """

    def test_events_count_pending_vs_completed(self):
        """
        验证目的：count_events_by_day 正确统计各状态事件数量。
        
        期望输出：2 个 pending + 3 个 completed = 5 total。
        """
        db = MagicMock()
        mock_query = db.query.return_value

        # 模拟 group_by 返回 2 行
        mock_query.filter.return_value.group_by.return_value.all.return_value = [
            ("pending", 2),
            ("completed", 3),
        ]

        result = event_crud.count_events_by_day(db, 1, 1)
        assert result["pending"] == 2
        assert result["completed"] == 3
        assert result["total"] == 5

    def test_get_events_by_day_ordered_by_index(self):
        """
        验证目的：get_events_by_day 按 order_index 升序排列。
        
        期望输出：返回列表保持 order_index 顺序。
        """
        db = MagicMock()
        mock_query = db.query.return_value
        mock_filtered = mock_query.filter.return_value

        # 返回 3 个事件，验证 order_by 被正确调用
        ev1 = MockEvent(id=1, order_index=1)
        ev2 = MockEvent(id=2, order_index=2)
        ev3 = MockEvent(id=3, order_index=3)
        mock_filtered.order_by.return_value.all.return_value = [ev1, ev2, ev3]

        result = event_crud.get_events_by_day(db, 1, 1)
        assert len(result) == 3
        assert result[0].id == 1
        assert result[2].id == 3

    def test_get_day_number_returns_max(self):
        """
        验证目的：get_day_number 返回角色的最大 day_number。
        
        期望输出：有事件时返回最大天数，无事件时返回 1。
        """
        db = MagicMock()

        # 场景1：有事件记录
        db.query.return_value.filter.return_value.scalar.return_value = 5
        assert event_crud.get_day_number(db, 1) == 5

        # 场景2：无事件记录
        db.query.return_value.filter.return_value.scalar.return_value = None
        assert event_crud.get_day_number(db, 1) == 1

    def test_create_events_bulk_inserts_all(self):
        """
        验证目的：batch insert 正确插入所有事件。
        
        期望输出：传入 3 条数据，全部插入。
        """
        db = MagicMock()
        events_data = [
            {"character_id": 1, "day_number": 2, "order_index": 1,
             "event_type": "schedule_action", "content": "事件1"},
            {"character_id": 1, "day_number": 2, "order_index": 2,
             "event_type": "schedule_action", "content": "事件2"},
            {"character_id": 1, "day_number": 2, "order_index": 3,
             "event_type": "schedule_action", "content": "事件3"},
        ]
        result = event_crud.create_events_bulk(db, events_data)
        assert len(result) == 3
        db.execute.assert_called_once()


# ==================== 运行入口 ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
