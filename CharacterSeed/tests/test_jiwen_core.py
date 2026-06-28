"""
jiwen 核心算法 pytest 测试
覆盖：
  - 五轴初始化
  - apply_delta
  - tick 推进 + 漂移率
  - 阈值触发（observation / contact / forceContact / find_activity / prideBlock）
  - get_prompt_context / get_style_guidance
  - 持久化 callback
"""
import pytest

from backend.jiwen.jiwen_core import (
    JiwenEngine,
    JiwenStateSnapshot,
    create_jiwen,
    DEFAULT_RATES,
    DEFAULT_THRESHOLDS,
    _default_connection_rate,
    _default_prompt_context,
    _default_style_guidance,
)


# ======================================================================
# 1) 基础初始化
# ======================================================================
def test_create_jiwen_default_state():
    jiwen = create_jiwen(character_id=1)
    state = jiwen.get_state()
    assert state["connection"] == 0.0
    assert state["pride"] == 0.0
    assert state["valence"] == 0.0
    assert state["arousal"] == 0.0
    assert state["immersion"] == 0.0
    assert state["user_status"] == "active"
    assert state["activity_type"] == "none"


def test_create_jiwen_with_custom_params():
    jiwen = create_jiwen(
        character_id=2,
        rates={"connectionAccel": 0.01, "accelDelay": 10},
        thresholds={"forceContact": 0.3},
    )
    assert jiwen.rates["connectionAccel"] == 0.01
    assert jiwen.thresholds["forceContact"] == 0.3


# ======================================================================
# 2) apply_delta
# ======================================================================
def test_apply_delta_basic():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"pride": -0.1, "valence": 0.2})
    s = jiwen.get_state()
    assert s["pride"] == pytest.approx(-0.1, abs=1e-6)
    assert s["valence"] == pytest.approx(0.2, abs=1e-6)


def test_apply_delta_mood_alias():
    """'mood' 应被识别为 valence 的别名"""
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"mood": 0.3})
    s = jiwen.get_state()
    assert s["valence"] == pytest.approx(0.3, abs=1e-6)


def test_apply_delta_clip_to_range():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"connection": 5.0})  # 远超上限
    s = jiwen.get_state()
    assert s["connection"] == 1.0
    jiwen.apply_delta({"pride": -5.0})  # 远超下限
    s = jiwen.get_state()
    assert s["pride"] == -1.0


def test_apply_delta_ignores_unknown_keys():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"unknown_axis": 0.5, "pride": 0.1})
    s = jiwen.get_state()
    assert s["pride"] == pytest.approx(0.1, abs=1e-6)
    # 'unknown_axis' 被忽略


# ======================================================================
# 3) tick 推进
# ======================================================================
def test_tick_zero_minutes_no_effect():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"pride": 0.5, "valence": 0.3})
    jiwen.tick(0)
    s = jiwen.get_state()
    assert s["pride"] == pytest.approx(0.5, abs=1e-6)
    assert s["valence"] == pytest.approx(0.3, abs=1e-6)


def test_tick_pride_regression_to_zero():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"pride": 0.5})
    initial = jiwen.get_state()["pride"]
    jiwen.tick(60)  # 60 min
    new_pride = jiwen.get_state()["pride"]
    # 应该向 0 回归
    assert new_pride < initial
    assert new_pride >= 0


def test_tick_valence_regression():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"valence": 0.5})
    initial = jiwen.get_state()["valence"]
    jiwen.tick(60)
    new_v = jiwen.get_state()["valence"]
    assert new_v < initial
    assert new_v > 0


def test_tick_immersion_decay():
    jiwen = create_jiwen(character_id=1)
    jiwen.set_activity("reading", "test")
    initial_imm = jiwen.get_state()["immersion"]
    assert initial_imm > 0  # 至少应该有 0.7 (reading 的 boost)
    jiwen.tick(60)  # 60 min
    new_imm = jiwen.get_state()["immersion"]
    assert new_imm < initial_imm


def test_tick_connection_growth():
    jiwen = create_jiwen(character_id=1)
    jiwen.set_last_chat_message_id(1, "短")  # 短消息 → 快增长
    jiwen.tick(60 * 12)  # 12h
    s = jiwen.get_state()
    assert s["connection"] > 0  # 应该有增长
    # 默认 0.0010/min × 720min = 0.72
    assert 0.5 < s["connection"] <= 1.0


def test_tick_updates_total_ticks():
    jiwen = create_jiwen(character_id=1)
    jiwen.tick(5)
    jiwen.tick(5)
    jiwen.tick(5)
    s = jiwen.get_state()
    assert s["total_ticks"] == 3


# ======================================================================
# 4) 阈值触发
# ======================================================================
def test_observation_trigger():
    jiwen = create_jiwen(
        character_id=1,
        rates={"connectionAccel": 0.05},  # 加速连接增长
    )
    jiwen.set_last_chat_message_id(1, "短")
    triggers = jiwen.tick(60 * 4)  # 4h 应该 observation
    actions = [t["action"] for t in triggers]
    assert "observation" in actions


def test_contact_trigger():
    jiwen = create_jiwen(
        character_id=1,
        rates={"connectionAccel": 0.05},
    )
    jiwen.set_last_chat_message_id(1, "短")
    triggers = jiwen.tick(60 * 12)  # 12h
    actions = [t["action"] for t in triggers]
    assert "contact" in actions or "observation" in actions


def test_force_contact_overrides_pride():
    """forceContact 应该无视 pride 阻断"""
    jiwen = create_jiwen(
        character_id=1,
        rates={"connectionAccel": 0.05},
    )
    jiwen.apply_delta({"pride": 0.9})  # 高骄傲
    jiwen.set_last_chat_message_id(1, "短")
    triggers = jiwen.tick(60 * 12)  # 高连接
    contact_triggers = [t for t in triggers if t["action"] == "contact"]
    assert len(contact_triggers) > 0
    assert any(t.get("forced") for t in contact_triggers)


def test_pride_block_contact():
    """高骄傲应阻断 contact（未达 forceContact 时）"""
    jiwen = create_jiwen(character_id=1)
    # 强制把 connection 推到 0.4（considerContact 0.35 ~ forceContact 0.50 之间）
    jiwen.apply_delta({"connection": 0.4, "pride": 0.8})
    triggers = jiwen.check_thresholds()
    actions = [t["action"] for t in triggers]
    # pride 高于 prideBlock 0.5 → 不应 contact
    assert "contact" not in actions
    # 应该 find_activity（被骄傲阻断）
    assert "find_activity" in actions


def test_valence_activity_trigger():
    """低 valence 应触发 find_activity"""
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"valence": -0.6})  # 低于 valenceActivity (-0.5)
    triggers = jiwen.check_thresholds()
    actions = [t["action"] for t in triggers]
    assert "find_activity" in actions


def test_arousal_agitation_trigger():
    """高 arousal 应触发 find_activity"""
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"arousal": 0.8})  # 高于 arousalAgitation 0.7
    triggers = jiwen.check_thresholds()
    actions = [t["action"] for t in triggers]
    assert "find_activity" in actions


# ======================================================================
# 5) reset_connection
# ======================================================================
def test_reset_connection():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"connection": 0.5})
    jiwen.reset_connection()
    assert jiwen.get_state()["connection"] == 0.0


# ======================================================================
# 6) set_activity
# ======================================================================
def test_set_activity_boosts_immersion():
    jiwen = create_jiwen(character_id=1)
    jiwen.set_activity("reading")
    s = jiwen.get_state()
    assert s["immersion"] >= 0.7
    assert s["activity_type"] == "reading"


def test_set_activity_invalid_uses_none():
    jiwen = create_jiwen(character_id=1)
    jiwen.set_activity("invalid_type")
    s = jiwen.get_state()
    assert s["activity_type"] == "none"


# ======================================================================
# 7) set_user_status
# ======================================================================
def test_set_user_status_valid():
    jiwen = create_jiwen(character_id=1)
    for status in ["active", "busy", "away", "sleeping"]:
        jiwen.set_user_status(status)
        assert jiwen.get_state()["user_status"] == status


def test_set_user_status_invalid_defaults_to_active():
    jiwen = create_jiwen(character_id=1)
    jiwen.set_user_status("invalid")
    assert jiwen.get_state()["user_status"] == "active"


# ======================================================================
# 8) get_prompt_context / get_style_guidance
# ======================================================================
def test_get_prompt_context_contains_text():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"connection": 0.6, "pride": 0.6})
    ctx = jiwen.get_prompt_context()
    assert isinstance(ctx, str)
    assert len(ctx) > 0


def test_get_prompt_context_high_connection_says_want_to_talk():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"connection": 0.6})
    ctx = jiwen.get_prompt_context()
    assert any(kw in ctx for kw in ["坐不住", "想", "找", "开口"])


def test_get_prompt_context_high_pride_says_endure():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"pride": 0.7})
    ctx = jiwen.get_prompt_context()
    assert "端" in ctx or "低" in ctx or "软" in ctx


def test_get_style_guidance_high_pride():
    jiwen = create_jiwen(character_id=1)
    jiwen.apply_delta({"pride": 0.7})
    style = jiwen.get_style_guidance()
    assert isinstance(style, str)
    assert len(style) > 0


def test_get_prompt_context_with_activity():
    jiwen = create_jiwen(character_id=1)
    jiwen.set_activity("reading", "哲学书")
    ctx = jiwen.get_prompt_context()
    assert "哲学书" in ctx or "阅读" in ctx or "看书" in ctx


# ======================================================================
# 9) 持久化 callback
# ======================================================================
def test_persistence_load_save_callbacks():
    saved_data = {}

    def on_save(state):
        saved_data.update(state)

    def on_load():
        return saved_data if saved_data else None

    jiwen = create_jiwen(character_id=1, on_save=on_save, on_load=on_load)
    loaded = jiwen.load()
    assert loaded is False  # 无历史

    jiwen.apply_delta({"pride": 0.3, "valence": 0.2})
    jiwen.save()

    # 新实例，模拟重启
    jiwen2 = create_jiwen(character_id=1, on_save=on_save, on_load=on_load)
    loaded = jiwen2.load()
    assert loaded is True
    s = jiwen2.get_state()
    assert s["pride"] == pytest.approx(0.3, abs=1e-6)
    assert s["valence"] == pytest.approx(0.2, abs=1e-6)


# ======================================================================
# 10) state summary
# ======================================================================
def test_get_state_summary_format():
    jiwen = create_jiwen(character_id=1)
    summary = jiwen.get_state_summary()
    assert "[积温]" in summary
    assert "c:" in summary
    assert "p:" in summary
    assert "v:" in summary
    assert "a:" in summary
    assert "i:" in summary
    assert "userStatus:" in summary


# ======================================================================
# 11) default connection rate
# ======================================================================
def test_default_connection_rate_晚安():
    rate = _default_connection_rate({"content": "晚安，去睡了"})
    assert rate < 0.001  # 慢


def test_default_connection_rate_短消息():
    rate = _default_connection_rate({"content": "嗯"})
    assert rate > 0.0007  # 快


def test_default_connection_rate_no_msg():
    rate = _default_connection_rate(None)
    assert rate == 0.0007  # 默认


# ======================================================================
# 12) tick 更新 last_tick_at
# ======================================================================
def test_tick_updates_last_tick_at():
    jiwen = create_jiwen(character_id=1)
    assert jiwen.get_state()["last_tick_at"] is None
    jiwen.tick(5)
    assert jiwen.get_state()["last_tick_at"] is not None


# ======================================================================
# 13) 触发器累加统计
# ======================================================================
def test_trigger_counters():
    jiwen = create_jiwen(
        character_id=1,
        rates={"connectionAccel": 0.05},
    )
    jiwen.set_last_chat_message_id(1, "短")
    jiwen.tick(60 * 4)
    jiwen.tick(60 * 4)
    jiwen.tick(60 * 4)
    s = jiwen.get_state()
    assert s["total_observation_triggers"] + s["total_contact_triggers"] > 0
