"""
C1: 事件管线单元测试 (v1.6)

测试覆盖:
  1. compute_personality_influence() — 人格加权函数纯逻辑
  2. validate_event_capabilities() — 能力白名单校验
  3. validate_event_actor_output() — Actor 事件模式输出校验
  4. InteractionPipeline._build_scene_context() — 场景上下文组装
  5. InteractionPipeline._format_today_schedule() — 日程格式化
  6. Event CRUD: update_event_content() / reorder_event()

设计考量:
  - compute_personality_influence 是纯数学函数，使用参数化测试覆盖极端人格组合
  - 能力校验使用白名单模式，测试边界情况（非法输入、空列表、缺失 complete_event）
  - 测试不依赖 LLM 或数据库，使用 Mock 隔离外部依赖
"""

import json
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 事件管线已从 interaction.py 移除（v2.0 重构），全部测试跳过
pytestmark = pytest.mark.skip(
    reason="事件管线（compute_personality_influence / validate_event_capabilities / "
           "FALLBACK_*_EVENT_OUTPUT 等）已在 v2.0 重构中移除，测试待按新架构重写",
)


# ============================================================================
# 1. compute_personality_influence() 人格加权函数测试
# ============================================================================

class TestPersonalityInfluence:
    """人格加权函数纯逻辑测试"""

    def _get_func(self):
        from backend.modules.interaction import compute_personality_influence
        return compute_personality_influence

    def test_balanced_personality(self):
        """均衡人格：所有维度 50，倾向应该接近均匀"""
        func = self._get_func()
        result = func({
            "courage": 50, "intelligence": 50, "sociability": 50,
            "empathy": 50, "loyalty": 50, "optimism": 50,
        })
        assert "人格倾向分析" in result
        assert "勇气50" in result
        assert "主动对话倾向" in result
        assert "修改计划倾向" in result
        # 均衡人格下 succeed 应是最可能的
        assert "succeed" in result

    def test_brave_intelligent_character(self):
        """高勇气高智力角色：应倾向 succeed + exceed"""
        func = self._get_func()
        result = func({
            "courage": 90, "intelligence": 90, "sociability": 50,
            "empathy": 50, "loyalty": 50, "optimism": 50,
        })
        assert "succeed=" in result
        assert "exceed=" in result
        # succeed 应该是最高百分比
        lines = result.split("\n")
        pct_line = [l for l in lines if "succeed=" in l]
        assert len(pct_line) > 0

    def test_timid_unsociable_character(self):
        """低勇气低社交角色：应倾向 linger"""
        func = self._get_func()
        result = func({
            "courage": 10, "intelligence": 50, "sociability": 10,
            "empathy": 50, "loyalty": 50, "optimism": 50,
        })
        assert "linger=" in result

    def test_disloyal_unempathetic_character(self):
        """低忠诚低同理心角色：应倾向 skip"""
        func = self._get_func()
        result = func({
            "courage": 50, "intelligence": 50, "sociability": 50,
            "empathy": 10, "loyalty": 10, "optimism": 50,
        })
        assert "skip=" in result

    def test_sociable_optimistic_character(self):
        """高社交高乐观角色：对话倾向应为高"""
        func = self._get_func()
        result = func({
            "courage": 50, "intelligence": 50, "sociability": 90,
            "empathy": 50, "loyalty": 50, "optimism": 90,
        })
        assert "主动对话倾向：高" in result

    def test_smart_disloyal_character(self):
        """高智力低忠诚：修改计划倾向应为高"""
        func = self._get_func()
        result = func({
            "courage": 50, "intelligence": 90, "sociability": 50,
            "empathy": 50, "loyalty": 10, "optimism": 50,
        })
        assert "修改计划倾向：高" in result

    def test_empty_personality(self):
        """空人格 dict：不崩溃，返回占位文本"""
        func = self._get_func()
        result = func({})
        assert "无足够人格数据" in result

    def test_none_personality(self):
        """None 人格：不崩溃"""
        func = self._get_func()
        result = func(None)
        assert "无足够人格数据" in result

    def test_partial_personality(self):
        """部分人格缺失：缺失维度用默认值 50"""
        func = self._get_func()
        result = func({"courage": 80})  # 只有 1 个维度
        assert "人格倾向分析" in result
        # 不崩溃即可，具体倾向由默认值决定

    def test_event_type_not_used(self):
        """event_type 参数不影响输出（当前未使用）"""
        func = self._get_func()
        r1 = func({"courage": 80}, "schedule_action")
        r2 = func({"courage": 80}, "scene_event")
        assert r1 == r2

    def test_extreme_bounds(self):
        """边界值：0 和 100 应正确归一化"""
        func = self._get_func()
        result = func({
            "courage": 0, "intelligence": 100, "sociability": 0,
            "empathy": 100, "loyalty": 0, "optimism": 100,
        })
        assert "人格倾向分析" in result
        assert "勇气0" in result
        assert "智力100" in result


# ============================================================================
# 2. validate_event_capabilities() — 能力白名单校验测试
# ============================================================================

class TestEventCapabilities:
    """Director 事件模式能力白名单校验"""

    def _get_func(self):
        from backend.services.llm_service import LLMService
        return LLMService.validate_event_capabilities

    def test_valid_basic_capabilities(self):
        """合法基础能力"""
        func = self._get_func()
        result = func(["respond_normally", "initiate_dialogue", "modify_plan"])
        assert "respond_normally" in result
        assert "initiate_dialogue" in result
        assert "modify_plan" in result

    def test_valid_complete_event_subtypes(self):
        """合法 complete_event 子类型"""
        func = self._get_func()
        for subtype in ["succeed", "exceed", "linger", "fail", "skip"]:
            result = func([f"complete_event({subtype})"])
            assert f"complete_event({subtype})" in result, f"子类型 {subtype} 应通过"

    def test_invalid_capability_filtered(self):
        """非法能力被过滤"""
        func = self._get_func()
        result = func(["fly_to_moon", "become_god"])
        # 非法输入被过滤，保底至少包含 respond_normally + complete_event(succeed)
        assert "fly_to_moon" not in result
        assert "respond_normally" in result
        assert any("complete_event(" in c for c in result)

    def test_invalid_complete_subtype_filtered(self):
        """非法的 complete_event 子类型被过滤"""
        func = self._get_func()
        result = func(["complete_event(ascend)", "complete_event(destroy)"])
        assert "complete_event(ascend)" not in result
        assert "complete_event(destroy)" not in result
        # 保底
        assert any("complete_event(" in c for c in result)

    def test_empty_list_fallback(self):
        """空列表返回保底值"""
        func = self._get_func()
        result = func([])
        assert "respond_normally" in result
        assert any("complete_event(" in c for c in result)

    def test_non_list_fallback(self):
        """非列表输入返回保底值"""
        func = self._get_func()
        result = func("not_a_list")
        assert "respond_normally" in result

    def test_none_fallback(self):
        """None 输入返回保底值"""
        func = self._get_func()
        result = func(None)
        assert "respond_normally" in result

    def test_missing_complete_event_added(self):
        """没有 complete_event 时自动补充"""
        func = self._get_func()
        result = func(["respond_normally"])
        has_complete = any(c.startswith("complete_event(") for c in result)
        assert has_complete, "应自动添加 complete_event"

    def test_duplicate_complete_event_kept(self):
        """多个 complete_event 子类型都保留"""
        func = self._get_func()
        result = func(["complete_event(succeed)", "complete_event(exceed)"])
        succeed_count = sum(1 for c in result if c == "complete_event(succeed)")
        exceed_count = sum(1 for c in result if c == "complete_event(exceed)")
        assert succeed_count >= 1
        assert exceed_count >= 1


# ============================================================================
# 3. validate_event_actor_output() — Actor 事件模式输出校验
# ============================================================================

class TestEventActorOutput:
    """Actor 事件模式输出格式校验"""

    def _get_func(self):
        from backend.services.llm_service import LLMService
        return LLMService.validate_event_actor_output

    def test_valid_full_output(self):
        """完整合法输出"""
        func = self._get_func()
        data = {
            "action": "她缓缓走向窗边，凝视远方",
            "expression": "若有所思",
            "speech": "今天真好",
            "dialogue_pending": {"content": "我想和你聊聊", "tone": "友好"},
        }
        result = func(data)
        assert result["action"] == "她缓缓走向窗边，凝视远方"
        assert result["expression"] == "若有所思"
        assert result["speech"] == "今天真好"
        assert result["dialogue_pending"] == {"content": "我想和你聊聊", "tone": "友好"}

    def test_speech_is_none(self):
        """speech 为 None 时合法（无对话对象）"""
        func = self._get_func()
        result = func({"action": "走向窗边", "expression": "平静", "speech": None})
        assert result["speech"] is None

    def test_speech_empty_string(self):
        """speech 为空字符串时转为 None"""
        func = self._get_func()
        result = func({"action": "做事", "expression": "专注", "speech": ""})
        assert result["speech"] is None

    def test_action_missing_fallback(self):
        """action 缺失时提供保底"""
        func = self._get_func()
        result = func({"expression": "微笑"})
        assert result["action"] == "按照计划处理了当前事件"

    def test_expression_missing_fallback(self):
        """expression 缺失时提供保底"""
        func = self._get_func()
        result = func({"action": "做事"})
        assert result["expression"] == "表情平静"

    def test_dialogue_pending_invalid_type(self):
        """dialogue_pending 非 dict 时转为 None"""
        func = self._get_func()
        result = func({
            "action": "做事", "expression": "平静",
            "dialogue_pending": "invalid",
        })
        assert result["dialogue_pending"] is None

    def test_not_dict_raises(self):
        """非 dict 输入抛出 ValueError"""
        func = self._get_func()
        with pytest.raises(ValueError):
            func("not a dict")


# ============================================================================
# 4. compute_personality_influence() 参数化测试
# ============================================================================

class TestPersonalityInfluenceParametrized:
    """参数化覆盖极端人格组合"""

    def _get_func(self):
        from backend.modules.interaction import compute_personality_influence
        return compute_personality_influence

    @pytest.mark.parametrize("personality,expected_keywords", [
        # 极值组合
        ({"courage": 100, "intelligence": 100, "sociability": 100,
          "empathy": 100, "loyalty": 100, "optimism": 100},
         ["succeed=", "主动对话倾向：高"]),
        ({"courage": 0, "intelligence": 0, "sociability": 0,
          "empathy": 0, "loyalty": 0, "optimism": 0},
         ["fail=", "主动对话倾向：低"]),
        # 混合极值
        ({"courage": 100, "intelligence": 10, "sociability": 10,
          "empathy": 10, "loyalty": 90, "optimism": 10},
         ["人格倾向分析"]),
        # 只有高社交高乐观
        ({"courage": 30, "intelligence": 30, "sociability": 95,
          "empathy": 30, "loyalty": 30, "optimism": 95},
         ["主动对话倾向：高"]),
    ])
    def test_extreme_combinations(self, personality, expected_keywords):
        """极值人格组合：至少输出包含预期关键词"""
        func = self._get_func()
        result = func(personality)
        for kw in expected_keywords:
            assert kw in result, f"人格={personality}, 应包含 '{kw}'"


# ============================================================================
# 5. _format_today_schedule() — 日程格式化测试
# ============================================================================

class TestFormatTodaySchedule:
    """日程格式化函数"""

    def _get_func(self):
        from backend.modules.interaction import InteractionPipeline
        return InteractionPipeline._format_today_schedule

    def test_empty_events(self):
        """无事件时日程显示占位文本"""
        func = self._get_func()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        result = func(1, 1, mock_db)
        assert "今日无安排" in result

    def test_with_events(self):
        """有事件时正确格式化"""
        func = self._get_func()
        mock_db = MagicMock()

        mock_event1 = MagicMock()
        mock_event1.order_index = 1
        mock_event1.time_period = "morning"
        mock_event1.event_type = "schedule_action"
        mock_event1.content = "去集市买菜"
        mock_event1.status = "pending"

        mock_event2 = MagicMock()
        mock_event2.order_index = 2
        mock_event2.time_period = "afternoon"
        mock_event2.event_type = "schedule_action"
        mock_event2.content = "拜访邻居"
        mock_event2.status = "completed"

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_event1, mock_event2
        ]

        result = func(1, 1, mock_db)
        assert "去集市买菜" in result
        assert "拜访邻居" in result
        assert "pending" in result or "⏳" in result


# ============================================================================
# 6. _build_scene_context() — 场景上下文组装测试
# ============================================================================

class TestBuildSceneContext:
    """场景上下文组装函数"""

    def _get_func(self):
        from backend.modules.interaction import InteractionPipeline
        return InteractionPipeline._build_scene_context

    def test_no_scene_id(self):
        """角色无 current_scene_id 时返回空字符串"""
        func = self._get_func()
        mock_char = MagicMock()
        mock_char.current_scene_id = None
        result = func(mock_char, MagicMock())
        assert result == ""

    @patch("backend.modules.interaction.scene_crud.get_scene_path")
    @patch("backend.modules.interaction.scene_crud.get_adjacent_scenes")
    @patch("backend.modules.interaction.scene_change_crud.get_recent_changes")
    def test_full_context(self, mock_changes, mock_adjacent, mock_path):
        """完整场景上下文组装"""
        func = self._get_func()
        mock_char = MagicMock()
        mock_char.current_scene_id = 1

        # Mock 场景路径
        mock_scene1 = MagicMock()
        mock_scene1.name = "艾泽拉斯"
        mock_scene1.scene_type = "world"
        mock_scene1.scene_layer = "conceptual"
        mock_scene1.description = "一个广阔的世界"

        mock_scene2 = MagicMock()
        mock_scene2.name = "暴风城"
        mock_scene2.scene_type = "city"
        mock_scene2.scene_layer = "actual"
        mock_scene2.description = "繁华的人类主城"

        mock_path.return_value = [mock_scene1, mock_scene2]

        # Mock 相邻场景
        mock_adj = MagicMock()
        mock_adj.name = "闪金镇"
        mock_adj.scene_type = "town"
        mock_adjacent.return_value = [mock_adj]

        # Mock 最近变化
        mock_change = MagicMock()
        mock_change.day_number = 1
        mock_change.description = "城门关闭了"
        mock_changes.return_value = [mock_change]

        result = func(mock_char, MagicMock())
        assert "艾泽拉斯" in result
        assert "暴风城" in result
        assert "闪金镇" in result
        assert "城门关闭了" in result

    @patch("backend.modules.interaction.scene_change_crud.get_recent_changes")
    @patch("backend.modules.interaction.scene_crud.get_adjacent_scenes")
    @patch("backend.modules.interaction.scene_crud.get_scene_path")
    def test_exception_handling(self, mock_path, mock_adjacent, mock_changes):
        """异常时返回空字符串而不崩溃"""
        func = self._get_func()
        mock_char = MagicMock()
        mock_char.current_scene_id = 1
        mock_path.side_effect = Exception("DB error")
        mock_adjacent.side_effect = Exception("DB error")
        mock_changes.side_effect = Exception("DB error")

        result = func(mock_char, MagicMock())
        assert result == ""  # 全部异常时优雅降级返回空


# ============================================================================
# 7. 集成测试：pipeline_result 结构校验
# ============================================================================

class TestRunEventResultStructure:
    """pipeline_result 字典结构校验"""

    REQUIRED_KEYS = [
        "action", "speech", "expression", "emotion", "goal",
        "capabilities", "event_attitude", "plan_modifications",
        "dialogue_pending", "director_raw", "actor_raw",
    ]

    def test_all_required_keys_present(self):
        """流水线返回结构必须包含所有必要字段"""
        # 模拟 run_event 的最小返回结构
        from backend.modules.interaction import (
            FALLBACK_DIRECTOR_EVENT_OUTPUT,
            FALLBACK_ACTOR_EVENT_OUTPUT,
        )

        # 构造最小合法 pipeline_result（模拟降级路径）
        pipeline_result = {
            "action": FALLBACK_ACTOR_EVENT_OUTPUT["action"],
            "speech": FALLBACK_ACTOR_EVENT_OUTPUT["speech"],
            "expression": FALLBACK_ACTOR_EVENT_OUTPUT["expression"],
            "emotion": FALLBACK_DIRECTOR_EVENT_OUTPUT["emotion"],
            "goal": FALLBACK_DIRECTOR_EVENT_OUTPUT["goal"],
            "capabilities": FALLBACK_DIRECTOR_EVENT_OUTPUT["capabilities"],
            "event_attitude": FALLBACK_DIRECTOR_EVENT_OUTPUT["event_attitude"],
            "plan_modifications": FALLBACK_DIRECTOR_EVENT_OUTPUT["plan_modifications"],
            "dialogue_pending": FALLBACK_ACTOR_EVENT_OUTPUT["dialogue_pending"],
            "director_raw": None,
            "actor_raw": None,
        }

        for key in self.REQUIRED_KEYS:
            assert key in pipeline_result, f"缺少必需字段: {key}"

    def test_capabilities_are_list(self):
        """capabilities 必须是列表"""
        from backend.services.llm_service import LLMService
        caps = LLMService.validate_event_capabilities(["respond_normally"])
        assert isinstance(caps, list)

    def test_plan_modifications_default_list(self):
        """plan_modifications 默认值为空列表"""
        from backend.modules.interaction import FALLBACK_DIRECTOR_EVENT_OUTPUT
        mods = FALLBACK_DIRECTOR_EVENT_OUTPUT.get("plan_modifications", [])
        assert isinstance(mods, list)


# ============================================================================
# 8. 降级常量测试
# ============================================================================

class TestFallbackConstants:
    """降级常量存在性和合法性"""

    def test_director_event_fallback(self):
        """Director 事件模式降级常量完整"""
        from backend.modules.interaction import FALLBACK_DIRECTOR_EVENT_OUTPUT
        for key in ["emotion", "goal", "capabilities", "event_attitude", "plan_modifications"]:
            assert key in FALLBACK_DIRECTOR_EVENT_OUTPUT, f"缺少 {key}"
        assert isinstance(FALLBACK_DIRECTOR_EVENT_OUTPUT["capabilities"], list)

    def test_actor_event_fallback(self):
        """Actor 事件模式降级常量完整"""
        from backend.modules.interaction import FALLBACK_ACTOR_EVENT_OUTPUT
        for key in ["action", "speech", "expression", "dialogue_pending"]:
            assert key in FALLBACK_ACTOR_EVENT_OUTPUT, f"缺少 {key}"

    def test_fallback_capabilities_valid(self):
        """降级的 capabilities 应能通过白名单校验"""
        from backend.services.llm_service import LLMService
        from backend.modules.interaction import FALLBACK_DIRECTOR_EVENT_OUTPUT
        caps = FALLBACK_DIRECTOR_EVENT_OUTPUT["capabilities"]
        validated = LLMService.validate_event_capabilities(caps)
        assert len(validated) >= 2  # respond_normally + complete_event


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
