"""
LLMService 单元测试（schema 校验 + call_with_messages 参数校验）

测试目标：
  1. validate_director_schema — 注意力聚焦输出校验
  2. validate_actor_schema — 行为生成输出校验
  3. validate_growth_schema — 成长分析输出校验
  4. validate_creation_schema — 角色创建输出校验
  5. call_with_messages — messages 校验（role, content）
  6. _validate_call_params — prompt/system_prompt/temperature/max_tokens 校验
  7. _extract_content — 响应内容安全提取
  8. parse_json_response — JSON 解析及兜底

预期运行方式：python -m pytest tests/test_llm_service.py -v
不依赖外部 LLM 调用或 API Key。
"""
import pytest
from unittest.mock import MagicMock, patch, ANY

from backend.services.llm_service import LLMService


# ==============================================================================
# Test Suite 1：validate_director_schema
# ==============================================================================

class TestValidateDirectorSchema:
    """注意力聚焦（Director）输出校验"""

    def test_valid_input(self):
        data = {
            "emotion": "怀念",
            "focus_memories": ["角色还记得玩家说的话"],
            "goal": "分享往事",
            "style": "轻柔的",
        }
        result = LLMService.validate_director_schema(data)
        assert result["emotion"] == "怀念"
        assert len(result["focus_memories"]) == 1

    def test_focus_memories_truncated_to_3(self):
        """focus_memories 超过 3 条 → 截断到 3 条"""
        data = {
            "emotion": "开心",
            "focus_memories": [str(i) for i in range(10)],
            "goal": "聊天",
            "style": "自然的",
        }
        result = LLMService.validate_director_schema(data)
        assert len(result["focus_memories"]) == 3

    def test_focus_memories_empty_strings_filtered(self):
        data = {
            "emotion": "平静",
            "focus_memories": ["", "有效记忆", None],
            "goal": "继续",
            "style": "温和",
        }
        result = LLMService.validate_director_schema(data)
        assert result["focus_memories"] == ["有效记忆"]

    def test_missing_fields_get_defaults(self):
        data = {}
        result = LLMService.validate_director_schema(data)
        assert result["emotion"] == "neutral"
        assert result["goal"] == "继续对话"
        assert result["style"] == "natural"
        assert result["focus_memories"] == []

    def test_focus_memories_not_a_list(self):
        data = {"emotion": "x", "focus_memories": "not_a_list", "goal": "y", "style": "z"}
        result = LLMService.validate_director_schema(data)
        assert result["focus_memories"] == []

    def test_input_not_a_dict(self):
        with pytest.raises(ValueError, match="必须是字典"):
            LLMService.validate_director_schema("not a dict")


# ==============================================================================
# Test Suite 2：validate_actor_schema
# ==============================================================================

class TestValidateActorSchema:
    """行为生成（Actor）输出校验"""

    def test_valid_input(self):
        data = {"action": "微笑", "expression": "友善", "speech": "你好！"}
        result = LLMService.validate_actor_schema(data)
        assert result["action"] == "微笑"
        assert result["speech"] == "你好！"

    def test_missing_fields_get_defaults(self):
        result = LLMService.validate_actor_schema({})
        assert result["action"] == "stand"
        assert result["expression"] == "neutral"
        assert result["speech"] == "..."

    def test_empty_strings_get_defaults(self):
        data = {"action": "", "expression": "", "speech": ""}
        result = LLMService.validate_actor_schema(data)
        assert result["action"] == "stand"
        assert result["expression"] == "neutral"
        assert result["speech"] == "..."

    def test_non_string_converted(self):
        data = {"action": 123, "expression": None, "speech": True}
        result = LLMService.validate_actor_schema(data)
        assert isinstance(result["action"], str)
        assert isinstance(result["expression"], str)
        assert isinstance(result["speech"], str)

    def test_input_not_a_dict(self):
        with pytest.raises(ValueError, match="必须是字典"):
            LLMService.validate_actor_schema(42)


# ==============================================================================
# Test Suite 3：validate_growth_schema
# ==============================================================================

class TestValidateGrowthSchema:
    """成长分析（Growth）输出校验"""

    def test_valid_input(self):
        data = {
            "personality_delta": {"optimism": 3, "courage": -2},
            "new_memories": [{"content": "角色成长了", "importance": 8}],
            "event_summary": "角色经历了一场冒险",
        }
        result = LLMService.validate_growth_schema(data)
        assert result["personality_delta"]["optimism"] == 3
        assert len(result["new_memories"]) == 1
        assert result["event_summary"] == "角色经历了一场冒险"

    def test_delta_clamped(self):
        """delta 超出 [-30, 30] 范围时被截断"""
        data = {
            "personality_delta": {"optimism": 100, "courage": -100},
            "new_memories": [],
            "event_summary": "测试",
        }
        result = LLMService.validate_growth_schema(data)
        assert result["personality_delta"]["optimism"] == 30
        assert result["personality_delta"]["courage"] == -30

    def test_new_memories_max_3(self):
        """new_memories 不超过 3 条"""
        data = {
            "personality_delta": {},
            "new_memories": [{"content": f"记忆{i}", "importance": 5} for i in range(10)],
            "event_summary": "测试",
        }
        result = LLMService.validate_growth_schema(data)
        assert len(result["new_memories"]) == 3

    def test_new_memories_invalid_entries_skipped(self):
        data = {
            "personality_delta": {},
            "new_memories": [
                {"content": "有效记忆", "importance": 7},
                {"content": "", "importance": 5},  # 空内容 → 跳过
                {"content": "另一个有效", "importance": 3},
                "不是字典",  # 非 dict → 跳过
                None,
            ],
            "event_summary": "测试",
        }
        result = LLMService.validate_growth_schema(data)
        assert len(result["new_memories"]) == 2
        assert result["new_memories"][0]["content"] == "有效记忆"

    def test_importance_clamped(self):
        data = {
            "personality_delta": {},
            "new_memories": [{"content": "记忆", "importance": 999}],
            "event_summary": "测试",
        }
        result = LLMService.validate_growth_schema(data)
        assert result["new_memories"][0]["importance"] == 10

    def test_missing_event_summary(self):
        result = LLMService.validate_growth_schema({
            "personality_delta": {},
            "new_memories": [],
        })
        assert result["event_summary"] == "角色经历了一次成长"


# ==============================================================================
# Test Suite 4：validate_creation_schema
# ==============================================================================

class TestValidateCreationSchema:
    """角色创建（Creation）输出校验"""

    def test_valid_input(self):
        data = {
            "name": "测试角色",
            "world_setting": "一个魔法世界",
            "personality": {"optimism": 70, "courage": 60, "empathy": 80, "loyalty": 50, "intelligence": 90, "sociability": 40},
            "current_state": {"location": "森林", "activity": "散步", "mood": "平静"},
        }
        result = LLMService.validate_creation_schema(data)
        assert result["name"] == "测试角色"
        assert result["personality"]["optimism"] == 70

    def test_missing_top_level_field(self):
        with pytest.raises(ValueError, match="缺少必填字段"):
            LLMService.validate_creation_schema({"name": "test"})

    def test_personality_values_clamped(self):
        data = {
            "name": "T",
            "world_setting": "W",
            "personality": {"optimism": 999, "courage": -1, "empathy": 50, "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {},
        }
        result = LLMService.validate_creation_schema(data)
        assert result["personality"]["optimism"] == 100
        assert result["personality"]["courage"] == 0

    def test_current_state_missing_fields_defaulted(self):
        data = {
            "name": "T",
            "world_setting": "W",
            "personality": {"optimism": 50, "courage": 50, "empathy": 50, "loyalty": 50, "intelligence": 50, "sociability": 50},
            "current_state": {},
        }
        result = LLMService.validate_creation_schema(data)
        assert result["current_state"]["location"] == ""
        assert result["current_state"]["activity"] == ""
        assert result["current_state"]["mood"] == ""


# ==============================================================================
# Test Suite 5：call_with_messages 参数校验
# ==============================================================================

class TestCallWithMessages:
    """测试 call_with_messages 的输入校验"""

    @pytest.fixture(autouse=True)
    def _init_service(self):
        """避免在 __init__ 时读取真实文件/环境变量"""
        with patch.object(LLMService, "reload_config", return_value=None):
            self.service = LLMService()
            self.service.client = MagicMock()
            self.service.model = "test-model"
            yield

    def test_valid_messages(self):
        self.service.call_with_messages([
            {"role": "user", "content": "hi"},
        ])
        self.service.client.chat.completions.create.assert_called_once()

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="非空列表"):
            self.service.call_with_messages([])

    def test_invalid_role(self):
        with pytest.raises(ValueError, match="role 必须是"):
            self.service.call_with_messages([{"role": "robot", "content": "hi"}])

    def test_missing_content(self):
        with pytest.raises(ValueError, match="content 必须是字符串"):
            self.service.call_with_messages([{"role": "user", "content": 123}])

    def test_temperature_out_of_range(self):
        with pytest.raises(ValueError, match="temperature 必须在"):
            self.service.call_with_messages([{"role": "user", "content": "hi"}], temperature=5.0)

    def test_max_tokens_out_of_range(self):
        with pytest.raises(ValueError, match="max_tokens 必须在"):
            self.service.call_with_messages([{"role": "user", "content": "hi"}], max_tokens=99999)


# ==============================================================================
# Test Suite 6：_validate_call_params
# ==============================================================================

class TestValidateCallParams:
    def test_empty_prompt_raises(self):
        with pytest.raises(ValueError, match="prompt 必须是非空字符串"):
            LLMService._validate_call_params(LLMService, "", None, 0.7, 1000)

    def test_temperature_out_of_range(self):
        with pytest.raises(ValueError, match="temperature 必须在"):
            LLMService._validate_call_params(LLMService, "hi", None, -1, 1000)

    def test_max_tokens_out_of_range(self):
        with pytest.raises(ValueError, match="max_tokens 必须在"):
            LLMService._validate_call_params(LLMService, "hi", None, 0.7, 0)


# ==============================================================================
# Test Suite 7：_extract_content
# ==============================================================================

class TestExtractContent:
    def test_valid_response(self):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Hello world"
        result = LLMService._extract_content(LLMService, mock_resp)
        assert result == "Hello world"

    def test_empty_response(self):
        result = LLMService._extract_content(LLMService, None)
        assert result == ""

    def test_no_choices(self):
        mock_resp = MagicMock()
        mock_resp.choices = []
        result = LLMService._extract_content(LLMService, mock_resp)
        assert result == ""

    def test_content_is_none(self):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = None
        result = LLMService._extract_content(LLMService, mock_resp)
        assert result == ""


# ==============================================================================
# Test Suite 8：parse_json_response
# ==============================================================================

class TestParseJsonResponse:
    def test_valid_json(self):
        result = LLMService.parse_json_response(LLMService, '{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="无法解析"):
            LLMService.parse_json_response(LLMService, "not json")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="响应为空"):
            LLMService.parse_json_response(LLMService, "")

    def test_extract_json_from_markdown(self):
        """从 markdown 代码块中提取 JSON"""
        text = '```json\n{"key": "value"}\n```'
        result = LLMService.parse_json_response(LLMService, text)
        assert result == {"key": "value"}
