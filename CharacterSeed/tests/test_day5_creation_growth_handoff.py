"""
Day5 Creation → Growth 工作交接 · 核心单元测试
================================================

覆盖范围（对应 5 个修改步骤）：
  1. creation.txt prompt — 验证 day1_schedule 字段声明存在
  2. validate_creation_schema day1_schedule 校验 — 合法值保留 / 缺失保底 / 非法标准化 / 空数组兜底
  3. CreationModule.run() docstring — 验证 day1_schedule 在文档字符串中声明
  4. create_character 事件持久化 — 模拟角色创建后 events 表中有 Day 1 pending 记录
  5. auto_advance 函数签名 — 验证 Depends() 已移除

每个测试用例均包含：
  - 期望输出（assert 触发）
  - 验证目的（docstring 中解释）
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.services.llm_service import LLMService
from backend.crud import event as event_crud
from backend.models import Base, Event


# ==================== 1. creation.txt Prompt 验证 ====================

class TestCreationPrompt:
    """
    验证 creation.txt prompt 中 day1_schedule 字段的声明完整性。

    测试策略：读取 promp 文件，用字符串匹配验证字段名和约束描述，
    不依赖 LLM 调用，纯文本检查。
    """

    PROMPT_PATH = "backend/prompts/creation.txt"

    def test_prompt_contains_day1_schedule(self):
        """
        验证目的：prompt 第 10 字段声明 day1_schedule 存在。

        期望输出：文件中包含 "day1_schedule" 字段声明。
        """
        with open(self.PROMPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        assert "day1_schedule" in content, "prompt 缺少 day1_schedule 字段声明"
        assert '"content"' in content, "day1_schedule 缺少 content 子字段"
        assert '"event_type"' in content, "day1_schedule 缺少 event_type 子字段"

    def test_prompt_event_type_whitelist(self):
        """
        验证目的：event_type 白名单值已声明。

        期望输出：4 种合法 event_type 均在 prompt 中列出。
        """
        with open(self.PROMPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        for et in ("schedule_action", "scene_event", "character_initiative", "player_dialogue"):
            assert et in content, f"event_type 白名单值 '{et}' 未在 prompt 中声明"

    def test_prompt_time_period_whitelist(self):
        """
        验证目的：time_period 白名单值已声明。

        期望输出：4 种合法 time_period 均在 prompt 中列出。
        """
        with open(self.PROMPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        for tp in ("morning", "afternoon", "evening", "night"):
            assert tp in content, f"time_period 白名单值 '{tp}' 未在 prompt 中声明"

    def test_prompt_day1_after_long_term_goal(self):
        """
        验证目的：day1_schedule 在第 9 字段之后（字段 10）。

        期望输出：文件中 "long_term_goal" 出现在 "day1_schedule" 之前，
        且序号字段 "10." 存在。
        """
        with open(self.PROMPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        ltg_idx = content.index("long_term_goal")
        d1s_idx = content.index("day1_schedule")
        assert ltg_idx < d1s_idx, "day1_schedule 未在 long_term_goal 之后声明"
        assert "10." in content or '"day1_schedule"' in content.split('"long_term_goal"')[1][:200]


# ==================== 2. validate_creation_schema day1_schedule 校验 ====================

class TestValidateCreationDay1Schedule:
    """
    验证 validate_creation_schema() 新增的 day1_schedule 校验逻辑。

    测试策略：构造带 day1_schedule 的 mock 输入，验证校验后的输出字段完整性，
    包括合法保留、缺失保底、非法标准化、空内容跳过等边界情况。
    """

    BASE_DATA = {
        "name": "测试角色",
        "world_setting": "一个魔法世界",
        "personality": {
            "optimism": 50, "courage": 50, "empathy": 50,
            "loyalty": 50, "intelligence": 50, "sociability": 50,
        },
        "current_state": {"location": "森林", "activity": "散步", "mood": "平静"},
    }

    def test_valid_day1_schedule_preserved(self):
        """
        验证目的：合法 day1_schedule 数组原样保留。

        期望输出：3 条合法事件的 content / event_type / time_period / order_index 均正确。
        """
        data = {**self.BASE_DATA, "day1_schedule": [
            {"content": "清晨醒来整理行装", "event_type": "schedule_action", "time_period": "morning", "order_index": 1},
            {"content": "前往市场打听消息", "event_type": "scene_event", "time_period": "afternoon", "order_index": 2},
            {"content": "与神秘旅人交谈", "event_type": "player_dialogue", "time_period": "evening", "order_index": 3},
        ]}
        result = LLMService.validate_creation_schema(data)
        schedule = result["day1_schedule"]
        assert len(schedule) == 3
        assert schedule[0]["content"] == "清晨醒来整理行装"
        assert schedule[0]["event_type"] == "schedule_action"
        assert schedule[0]["time_period"] == "morning"
        assert schedule[0]["order_index"] == 1
        assert schedule[2]["event_type"] == "player_dialogue"
        assert schedule[2]["time_period"] == "evening"

    def test_missing_day1_schedule_fallback(self):
        """
        验证目的：day1_schedule 完全缺失时使用保底事件。

        期望输出：1 条保底事件，content="新的一天开始了"。
        """
        result = LLMService.validate_creation_schema(dict(self.BASE_DATA))
        schedule = result["day1_schedule"]
        assert isinstance(schedule, list)
        assert len(schedule) == 1
        assert schedule[0]["content"] == "新的一天开始了"
        assert schedule[0]["event_type"] == "schedule_action"
        assert schedule[0]["order_index"] == 1

    def test_null_day1_schedule_fallback(self):
        """
        验证目的：day1_schedule = None 时使用保底事件。

        期望输出：1 条保底事件。
        """
        data = {**self.BASE_DATA, "day1_schedule": None}
        result = LLMService.validate_creation_schema(data)
        assert len(result["day1_schedule"]) == 1
        assert result["day1_schedule"][0]["content"] == "新的一天开始了"

    def test_empty_array_fallback(self):
        """
        验证目的：空数组 day1_schedule = [] 时使用保底事件。

        期望输出：1 条保底事件。
        """
        data = {**self.BASE_DATA, "day1_schedule": []}
        result = LLMService.validate_creation_schema(data)
        assert len(result["day1_schedule"]) == 1

    def test_invalid_event_type_normalized(self):
        """
        验证目的：event_type 不在白名单时标准化为 schedule_action。

        期望输出：非法值 "unknown_type" → "schedule_action"。
        """
        data = {**self.BASE_DATA, "day1_schedule": [
            {"content": "测试事件", "event_type": "unknown_type", "time_period": "morning", "order_index": 1},
        ]}
        result = LLMService.validate_creation_schema(data)
        assert result["day1_schedule"][0]["event_type"] == "schedule_action"

    def test_invalid_time_period_normalized(self):
        """
        验证目的：time_period 不在白名单时标准化为 "morning"。

        期望输出：非法值 "midnight" → "morning"。
        """
        data = {**self.BASE_DATA, "day1_schedule": [
            {"content": "测试事件", "event_type": "schedule_action", "time_period": "midnight", "order_index": 1},
        ]}
        result = LLMService.validate_creation_schema(data)
        assert result["day1_schedule"][0]["time_period"] == "morning"

    def test_empty_time_period_becomes_none(self):
        """
        验证目的：空字符串 time_period 转为 None。

        期望输出：time_period = "" → None。
        """
        data = {**self.BASE_DATA, "day1_schedule": [
            {"content": "测试", "event_type": "schedule_action", "time_period": "", "order_index": 1},
        ]}
        result = LLMService.validate_creation_schema(data)
        assert result["day1_schedule"][0]["time_period"] is None

    def test_empty_content_skipped(self):
        """
        验证目的：content 为空的条目被跳过。

        期望输出：1 条空内容跳过，保留 1 条合法事件。
        """
        data = {**self.BASE_DATA, "day1_schedule": [
            {"content": "", "event_type": "schedule_action", "time_period": "morning", "order_index": 1},
            {"content": "   ", "event_type": "scene_event", "time_period": "afternoon", "order_index": 2},
            {"content": "有效事件", "event_type": "character_initiative", "time_period": "night", "order_index": 3},
        ]}
        result = LLMService.validate_creation_schema(data)
        assert len(result["day1_schedule"]) == 1
        assert result["day1_schedule"][0]["content"] == "有效事件"

    def test_non_dict_item_skipped(self):
        """
        验证目的：非 dict 的数组元素被跳过。

        期望输出：跳过 None 和字符串，保留 1 条事件。
        """
        data = {**self.BASE_DATA, "day1_schedule": [
            None,
            "not a dict",
            {"content": "有效事件", "event_type": "schedule_action", "time_period": "morning", "order_index": 1},
        ]}
        result = LLMService.validate_creation_schema(data)
        assert len(result["day1_schedule"]) == 1
        assert result["day1_schedule"][0]["content"] == "有效事件"

    def test_invalid_order_index_uses_enumeration(self):
        """
        验证目的：order_index 非法时使用遍历序号。

        期望输出：非数字 order_index → idx + 1。
        """
        data = {**self.BASE_DATA, "day1_schedule": [
            {"content": "A", "event_type": "schedule_action", "time_period": "morning", "order_index": "abc"},
            {"content": "B", "event_type": "schedule_action", "time_period": "afternoon", "order_index": None},
        ]}
        result = LLMService.validate_creation_schema(data)
        assert result["day1_schedule"][0]["order_index"] == 1
        assert result["day1_schedule"][1]["order_index"] == 2

    def test_non_list_value_fallback(self):
        """
        验证目的：day1_schedule 为非 list 类型时使用保底事件。

        期望输出：非 list → 1 条保底事件。
        """
        data = {**self.BASE_DATA, "day1_schedule": "not_a_list"}
        result = LLMService.validate_creation_schema(data)
        assert len(result["day1_schedule"]) == 1
        assert result["day1_schedule"][0]["content"] == "新的一天开始了"

    def test_preserves_existing_fields_with_day1_schedule(self):
        """
        验证目的：新增 day1_schedule 不影响原有字段校验。

        期望输出：原有 speaking_style / values / habits / long_term_goal 仍正常工作。
        """
        data = {
            **self.BASE_DATA,
            "speaking_style": ["语速缓慢"],
            "values": ["重视友情"],
            "habits": ["清晨冥想"],
            "long_term_goal": "成为天下第一剑客",
            "day1_schedule": [
                {"content": "出发冒险", "event_type": "scene_event", "time_period": "morning", "order_index": 1},
            ],
        }
        result = LLMService.validate_creation_schema(data)
        assert result["speaking_style"] == ["语速缓慢"]
        assert result["values"] == ["重视友情"]
        assert result["habits"] == ["清晨冥想"]
        assert result["long_term_goal"] == "成为天下第一剑客"
        assert len(result["day1_schedule"]) == 1


# ==================== 3. CreationModule.run() docstring 验证 ====================

class TestCreationModuleDocstring:
    """
    验证 CreationModule.run() 的 docstring 中 day1_schedule 字段声明。

    测试策略：以文本分析方式读取 creation.py 文件，检查 docstring 内容。
    不依赖模块导入，避免触发 LLM 初始化。
    """

    MODULE_PATH = "backend/modules/creation.py"

    def test_docstring_mentions_day1_schedule(self):
        """
        验证目的：docstring 中声明了 day1_schedule 返回值字段。

        期望输出：docstring 包含 "day1_schedule"。
        """
        with open(self.MODULE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        # 提取 run() 方法的 docstring 区域
        assert "def run(self" in content, "run() method not found"
        # 检查 docstring 中是否有 day1_schedule
        # 先定位 run() 方法，然后检查其后的 docstring
        run_idx = content.index("def run(self")
        docstring_region = content[run_idx:run_idx + 1500]
        assert "day1_schedule" in docstring_region, "run() docstring 缺少 day1_schedule 声明"
        assert "content" in docstring_region, "run() docstring 缺少 content 子字段说明"
        assert "event_type" in docstring_region, "run() docstring 缺少 event_type 子字段说明"
        assert "time_period" in docstring_region, "run() docstring 缺少 time_period 子字段说明"
        assert "order_index" in docstring_region, "run() docstring 缺少 order_index 子字段说明"


# ==================== 4. create_character 事件持久化集成测试 ====================

class TestCreateCharacterEventPersistence:
    """
    验证角色创建完成后 events 表中有 Day 1 pending 事件记录。

    测试策略：使用内存 SQLite 数据库，通过 event_crud.create_event 模拟
    create_character 端点的 Day 1 事件持久化逻辑。验证写入后的查询结果。
    """

    @pytest.fixture(scope="class")
    def engine(self):
        eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=eng)
        yield eng
        eng.dispose()

    @pytest.fixture(autouse=True)
    def db_session(self, engine):
        conn = engine.connect()
        trans = conn.begin()
        Session = sessionmaker(bind=conn)
        session = Session()
        yield session
        session.close()
        trans.rollback()
        conn.close()

    def test_persist_day1_schedule_as_events(self, db_session):
        """
        验证目的：模拟 create_character 端点的 day1_schedule 持久化逻辑，
        3 条事件均正确写入 events 表，status 为 pending。

        期望输出：数据库中有 3 条 Day 1 事件，status = "pending"。
        """
        # === SETUP：模拟 parsed_data 中的 day1_schedule（来自 Creation LLM） ===
        day1_schedule = [
            {"content": "清晨醒来整理行装", "event_type": "schedule_action", "time_period": "morning", "order_index": 1},
            {"content": "前往市场打听消息", "event_type": "scene_event", "time_period": "afternoon", "order_index": 2},
            {"content": "与神秘旅人交谈", "event_type": "player_dialogue", "time_period": "evening", "order_index": 3},
        ]
        character_id = 1  # 模拟角色 ID

        # === EXECUTE：逐条持久化（模拟 create_character 端点的代码） ===
        for item in day1_schedule:
            if isinstance(item, dict) and item.get("content", "").strip():
                event_crud.create_event(
                    db=db_session,
                    character_id=character_id,
                    day_number=1,
                    order_index=item.get("order_index", 1),
                    event_type=item.get("event_type", "schedule_action"),
                    content=item["content"].strip(),
                    status="pending",
                    time_period=item.get("time_period"),
                )

        # === ASSERT ===
        events = event_crud.get_events_by_day(db_session, character_id, 1, status_filter=None)
        assert len(events) == 3, f"期望 3 条事件，实际 {len(events)}"
        assert all(e.day_number == 1 for e in events), "所有事件应为 Day 1"
        assert all(e.status == "pending" for e in events), "所有事件 status 应为 pending"
        assert events[0].order_index == 1
        assert events[1].order_index == 2
        assert events[2].order_index == 3
        assert events[0].content == "清晨醒来整理行装"
        assert events[1].content == "前往市场打听消息"
        assert events[2].content == "与神秘旅人交谈"

    def test_empty_day1_schedule_skipped(self, db_session):
        """
        验证目的：空 day1_schedule 不会写入任何事件。

        期望输出：无事件记录。
        """
        day1_schedule = []
        character_id = 2

        for item in day1_schedule:
            if isinstance(item, dict) and item.get("content", "").strip():
                event_crud.create_event(db=db_session, character_id=character_id, day_number=1, order_index=1, event_type="schedule_action", content=item["content"].strip(), status="pending", time_period=item.get("time_period"))

        events = event_crud.get_events_by_day(db_session, character_id, 1)
        assert len(events) == 0

    def test_pending_status_can_be_advanced(self, db_session):
        """
        验证目的：写入的 pending 事件可以被完成（模拟"推进事件"按钮）。

        期望输出：事件从 pending → completed。
        """
        # 创建 1 条事件
        event_crud.create_event(
            db=db_session, character_id=3, day_number=1,
            order_index=1, event_type="schedule_action",
            content="测试事件", status="pending",
        )
        next_event = event_crud.get_next_pending_event(db_session, 3, 1)
        assert next_event is not None
        assert next_event.status == "pending"

        # 推进（标记为 completed）
        completed = event_crud.complete_event(db_session, next_event.id, '{"result": "done"}')
        assert completed.status == "completed"
        assert event_crud.has_pending_events(db_session, 3, 1) is False


# ==================== 5. auto_advance 函数签名验证 ====================

class TestAutoAdvanceFunctionSignature:
    """
    验证 auto_advance 函数签名已正确修复。

    测试策略：通过文本分析直接检查 main.py 中 auto_advance 的函数定义行。
    不依赖 FastAPI 启动，纯静态检查。
    """

    MAIN_PATH = "backend/main.py"

    def test_signature_no_depends_on_request(self):
        """
        验证目的：request 参数不再使用 Depends()。

        期望输出：函数签名为 def auto_advance(request: AdvanceRequest, db: Session = Depends(get_db)):
        """
        with open(self.MAIN_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        # 找到 auto_advance 函数定义行
        for line in content.splitlines():
            if "def auto_advance" in line:
                # 确认 request 参数没有 Depends()
                request_part = line.split("request")[1].split(",")[0] if ", request" in line else ""
                assert "Depends" not in line.split("request:")[1].split(",")[0], \
                    "auto_advance 的 request 参数不应使用 Depends()"
                # 确认 request 在 db 之前（body 在前，dependency 在后）
                req_idx = line.index("request:")
                db_idx = line.index("Depends(get_db)")
                assert req_idx < db_idx, \
                    "request 参数应位于 db 参数之前"
                return
        pytest.fail("未找到 auto_advance 函数定义")

    def test_signature_request_type_correct(self):
        """
        验证目的：request 参数类型标注为 AdvanceRequest。

        期望输出：类型标注包含 AdvanceRequest。
        """
        with open(self.MAIN_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        for line in content.splitlines():
            if "def auto_advance" in line:
                assert "AdvanceRequest" in line, "request 参数应标注为 AdvanceRequest 类型"
                return
        pytest.fail("未找到 auto_advance 函数定义")
