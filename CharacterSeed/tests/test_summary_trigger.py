"""
自适应摘要触发器（summary_trigger）pytest 测试

覆盖：
  1. 阈值检测（对话数达到上限触发 overflow）
  2. 摘要生成主流程（create_summary + mock LLM）
  3. 无对话的边界情况
  4. Character.config 角色级阈值覆盖
  以及 build_summary / get_active_summaries / forgotten_ratio / time_gap 等分支。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.models import Conversation, Memory, MemorySummary
from backend.modules.summary_trigger import (
    FORGOTTEN_RATIO_TRIGGER,
    MAX_MESSAGES_BETWEEN,
    MIN_MESSAGES_BETWEEN,
    TIME_GAP_DAYS,
    build_summary,
    create_summary,
    get_active_summaries,
    get_summary_config,
    should_summarize,
)


# ======================================================================
# 测试辅助
# ======================================================================
def _add_conversations(db, character_id: int, n: int, prefix: str = "msg") -> None:
    """批量插入 n 条对话"""
    for i in range(n):
        db.add(Conversation(
            character_id=character_id,
            user_input=f"{prefix}-{i}",
            npc_response=f"reply-{i}",
        ))
    db.commit()


class FakeLLM:
    """替身 LLMService：记录调用并返回固定摘要文本"""

    def __init__(self, response: str = "这是模拟摘要文本。"):
        self.response = response
        self.calls = []

    def call(self, prompt: str, system_prompt: str = "", temperature: float = 0.3,
             task: str = "", **kwargs) -> str:
        self.calls.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "task": task,
        })
        return self.response


# ======================================================================
# 1) 阈值检测：对话数达到上限触发 overflow
# ======================================================================
def test_should_summarize_overflow_default_threshold(db_session, sample_character):
    """对话数达到 MAX_MESSAGES_BETWEEN（100）应触发 overflow"""
    _add_conversations(db_session, sample_character.id, MAX_MESSAGES_BETWEEN)

    decision = should_summarize(db_session, sample_character.id)

    assert decision["should"] is True
    assert "msg_count_overflow" in decision["reason"]
    assert decision["msg_count_since_last"] == MAX_MESSAGES_BETWEEN
    # 100 >= 100 命中首个分支，无需依赖 forgotten_ratio / time_gap
    assert str(MAX_MESSAGES_BETWEEN) in decision["reason"]


def test_should_summarize_below_max_no_other_signal(db_session, sample_character):
    """
    对话数介于 min 与 max 之间、无 forgotten、无时间间隔时不应触发。
    通过角色 config 把 min 调低、并制造一个"刚刚生成"的活跃摘要使 time_since≈0，
    从而隔离出"仅靠对话数不足以触发"的情形。
    """
    # 角色级 config：min=2, max=100
    sample_character.config = json.dumps({"summary": {
        "min_messages_between": 2,
        "max_messages_between": 100,
    }})
    db_session.commit()

    # 一条"刚刚生成"的活跃摘要，使 anchor_time ≈ now → time_since≈0
    now = datetime(2026, 6, 29, 12, 0, 0)
    prior = MemorySummary(
        character_id=sample_character.id,
        summary_text="旧摘要",
        msg_start_id=0,
        msg_end_id=0,
        msg_count=0,
        is_active=1,
        trigger_reason="initial",
    )
    prior.created_at = now
    db_session.add(prior)
    db_session.commit()

    # anchor_id=0 → 统计全部对话；3 条 < 100，forgotten=0，time_since≈0 → 不触发
    _add_conversations(db_session, sample_character.id, 3)

    decision = should_summarize(db_session, sample_character.id, now=now)

    assert decision["should"] is False
    assert decision["reason"] == "no trigger"
    assert decision["msg_count_since_last"] == 3


# ======================================================================
# 2) 摘要生成主流程
# ======================================================================
def test_create_summary_generates_summary_with_mock_llm(db_session, sample_character):
    """create_summary 在触发后应生成 MemorySummary 行并返回摘要字典"""
    # 用 config 降低阈值，避免造 100 条对话
    sample_character.config = json.dumps({"summary": {
        "min_messages_between": 2,
        "max_messages_between": 3,
    }})
    db_session.commit()
    _add_conversations(db_session, sample_character.id, 3)

    fake_llm = FakeLLM(response="模拟的对话摘要。")
    result = create_summary(db_session, sample_character.id,
                            trigger_reason="adaptive", llm_service=fake_llm)

    # 返回字典字段齐全
    assert result is not None
    assert result["summary_text"] == "模拟的对话摘要。"
    assert result["msg_count"] >= 1
    assert result["id"] is not None
    # LLM 被调用一次，task 为 summary
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["task"] == "summary"

    # DB 中确实存在一条 active 摘要
    row = db_session.query(MemorySummary).filter(
        MemorySummary.character_id == sample_character.id,
        MemorySummary.is_active == 1,
    ).first()
    assert row is not None
    assert row.summary_text == "模拟的对话摘要。"
    assert row.trigger_reason == "adaptive"


def test_create_summary_supersedes_previous(db_session, sample_character):
    """新摘要生成后，旧 active 摘要应被标记为 superseded（is_active=0）"""
    sample_character.config = json.dumps({"summary": {
        "min_messages_between": 2,
        "max_messages_between": 3,
    }})
    db_session.commit()

    # 旧摘要
    old = MemorySummary(
        character_id=sample_character.id,
        summary_text="旧摘要",
        msg_start_id=0,
        msg_end_id=0,
        msg_count=0,
        is_active=1,
        trigger_reason="initial",
    )
    db_session.add(old)
    db_session.commit()
    old_id = old.id

    _add_conversations(db_session, sample_character.id, 3)
    fake_llm = FakeLLM(response="新摘要。")
    result = create_summary(db_session, sample_character.id, llm_service=fake_llm)

    assert result is not None
    db_session.refresh(old)
    assert old.is_active == 0
    assert old.superseded_by == result["id"]
    assert result["id"] != old_id


# ======================================================================
# 3) 无对话的边界情况
# ======================================================================
def test_should_summarize_no_conversations_too_few(db_session, sample_character):
    """无对话时：msg_count=0 < min → 不触发，reason=too_few"""
    decision = should_summarize(db_session, sample_character.id)

    assert decision["should"] is False
    assert "msg_count_too_few" in decision["reason"]
    assert decision["msg_count_since_last"] == 0
    # 无记忆 → forgotten_ratio=0
    assert decision["forgotten_ratio"] == 0.0


def test_create_summary_returns_none_when_no_conversations(db_session, sample_character):
    """无对话时 create_summary 应返回 None"""
    fake_llm = FakeLLM()
    result = create_summary(db_session, sample_character.id, llm_service=fake_llm)
    assert result is None
    # 未触发 → 不应调用 LLM
    assert len(fake_llm.calls) == 0


def test_build_summary_empty_conversations(db_session, sample_character):
    """build_summary 在区间内无对话时应返回空串且不调用 LLM"""
    fake_llm = FakeLLM()
    text = build_summary(
        db_session, sample_character.id,
        msg_start_id=0, msg_end_id=999,
        llm_service=fake_llm,
    )
    assert text == ""
    assert len(fake_llm.calls) == 0


def test_build_summary_empty_when_responses_blank(db_session, sample_character):
    """对话存在但 user_input/npc_response 均空 → lines 为空 → 返回空串"""
    db_session.add(Conversation(
        character_id=sample_character.id,
        user_input="   ",
        npc_response="   ",
    ))
    db_session.commit()
    fake_llm = FakeLLM()
    text = build_summary(
        db_session, sample_character.id,
        msg_start_id=0, msg_end_id=1,
        llm_service=fake_llm,
    )
    assert text == ""
    assert len(fake_llm.calls) == 0


# ======================================================================
# 4) Character.config 角色级阈值覆盖
# ======================================================================
def test_get_summary_config_default_values(db_session, sample_character):
    """无 config 时返回模块默认常量"""
    cfg = get_summary_config(db_session, sample_character.id)
    assert cfg["min_messages_between"] == MIN_MESSAGES_BETWEEN
    assert cfg["max_messages_between"] == MAX_MESSAGES_BETWEEN
    assert cfg["forgotten_ratio_trigger"] == FORGOTTEN_RATIO_TRIGGER
    assert cfg["time_gap_days"] == TIME_GAP_DAYS


def test_get_summary_config_character_override(db_session, sample_character):
    """Character.config.summary.* 应覆盖默认值"""
    sample_character.config = json.dumps({"summary": {
        "min_messages_between": 5,
        "max_messages_between": 50,
        "forgotten_ratio_trigger": 0.5,
        "time_gap_days": 14,
    }})
    db_session.commit()

    cfg = get_summary_config(db_session, sample_character.id)
    assert cfg["min_messages_between"] == 5
    assert cfg["max_messages_between"] == 50
    assert cfg["forgotten_ratio_trigger"] == 0.5
    assert cfg["time_gap_days"] == 14


def test_get_summary_config_invalid_json_falls_back(db_session, sample_character):
    """config 为非法 JSON 时应静默回退到默认值"""
    sample_character.config = "{not valid json"
    db_session.commit()
    cfg = get_summary_config(db_session, sample_character.id)
    assert cfg["min_messages_between"] == MIN_MESSAGES_BETWEEN
    assert cfg["max_messages_between"] == MAX_MESSAGES_BETWEEN


def test_should_summarize_respects_config_min_threshold(db_session, sample_character):
    """
    覆盖 min=5 后，3 条对话应命中 too_few 分支，
    reason 中出现 "< 5" 证明用的是覆盖后的阈值而非默认 20。
    """
    sample_character.config = json.dumps({"summary": {
        "min_messages_between": 5,
        "max_messages_between": 100,
    }})
    db_session.commit()
    _add_conversations(db_session, sample_character.id, 3)

    decision = should_summarize(db_session, sample_character.id)

    assert decision["should"] is False
    assert "msg_count_too_few" in decision["reason"]
    # 关键：reason 中是覆盖后的 5，而非默认 20
    assert "< 5" in decision["reason"]
    assert "< 20" not in decision["reason"]


# ======================================================================
# 5) forgotten_ratio 触发分支
# ======================================================================
def test_should_summarize_forgotten_ratio_trigger(db_session, sample_character):
    """forgotten_ratio > 阈值且对话数达到 min 时应触发 forgotten_ratio 分支"""
    # 降低 min 以便少量对话即可进入 forgotten 判定分支
    sample_character.config = json.dumps({"summary": {
        "min_messages_between": 2,
        "max_messages_between": 100,
    }})
    db_session.commit()

    # 4 条记忆，2 条 forgotten → ratio=0.5 > 0.3
    for i in range(2):
        db_session.add(Memory(
            character_id=sample_character.id, content=f"active-{i}",
            importance=5, strength=8, forgotten=0,
        ))
    for i in range(2):
        db_session.add(Memory(
            character_id=sample_character.id, content=f"forgot-{i}",
            importance=2, strength=1, forgotten=1,
        ))
    db_session.commit()

    # 3 条对话 >= min=2，< max=100；time_since 用 now 控制为 0 以隔离分支
    _add_conversations(db_session, sample_character.id, 3)
    now = datetime(2026, 6, 29, 12, 0, 0)

    # 构造一个"刚刚生成"的活跃摘要使 time_since≈0，避免落入 time_gap 分支
    prior = MemorySummary(
        character_id=sample_character.id,
        summary_text="旧", msg_start_id=0, msg_end_id=0,
        msg_count=0, is_active=1, trigger_reason="initial",
    )
    prior.created_at = now
    db_session.add(prior)
    db_session.commit()

    decision = should_summarize(db_session, sample_character.id, now=now)

    assert decision["should"] is True
    assert "forgotten_ratio" in decision["reason"]
    assert decision["forgotten_ratio"] == pytest.approx(0.5, abs=1e-6)


# ======================================================================
# 6) time_gap 触发分支
# ======================================================================
def test_should_summarize_time_gap_trigger(db_session, sample_character):
    """距上次摘要 > time_gap_days 且对话数达 min 时应触发 time_gap 分支"""
    sample_character.config = json.dumps({"summary": {
        "min_messages_between": 2,
        "max_messages_between": 100,
    }})
    db_session.commit()

    now = datetime(2026, 6, 29, 12, 0, 0)
    old_time = now - timedelta(days=TIME_GAP_DAYS + 3)  # 超过 7 天

    # 一条旧的活跃摘要（anchor_time = 10 天前）
    prior = MemorySummary(
        character_id=sample_character.id,
        summary_text="旧摘要", msg_start_id=0, msg_end_id=0,
        msg_count=0, is_active=1, trigger_reason="initial",
    )
    prior.created_at = old_time
    db_session.add(prior)
    db_session.commit()

    # 3 条对话 >= min=2，< max=100；无记忆 → forgotten=0；time_since > 7
    _add_conversations(db_session, sample_character.id, 3)

    decision = should_summarize(db_session, sample_character.id, now=now)

    assert decision["should"] is True
    assert "time_gap" in decision["reason"]
    assert decision["time_since_last_days"] > TIME_GAP_DAYS


# ======================================================================
# 7) build_summary 调用 LLM
# ======================================================================
def test_build_summary_with_mock_llm(db_session, sample_character):
    """build_summary 应拼接对话并调用 LLM，返回 strip 后的文本"""
    db_session.add(Conversation(
        character_id=sample_character.id,
        user_input="你好",
        npc_response="你好呀",
    ))
    db_session.add(Conversation(
        character_id=sample_character.id,
        user_input="今天天气不错",
        npc_response="是啊，适合散步",
    ))
    db_session.commit()
    latest_id = db_session.query(Conversation).filter(
        Conversation.character_id == sample_character.id
    ).order_by(Conversation.id.desc()).first().id

    fake_llm = FakeLLM(response="  用户与角色闲聊了天气。  ")
    text = build_summary(
        db_session, sample_character.id,
        msg_start_id=0, msg_end_id=latest_id,
        llm_service=fake_llm,
    )

    assert text == "用户与角色闲聊了天气。"
    assert len(fake_llm.calls) == 1
    # prompt 中应包含两条对话内容
    assert "你好" in fake_llm.calls[0]["prompt"]
    assert "散步" in fake_llm.calls[0]["prompt"]


def test_build_summary_llm_exception_returns_empty(db_session, sample_character):
    """LLM 抛异常时 build_summary 应返回空串而非传播异常"""
    db_session.add(Conversation(
        character_id=sample_character.id,
        user_input="hi", npc_response="hello",
    ))
    db_session.commit()

    boom = MagicMock()
    boom.call.side_effect = RuntimeError("LLM 不可用")
    text = build_summary(
        db_session, sample_character.id,
        msg_start_id=0, msg_end_id=1,
        llm_service=boom,
    )
    assert text == ""


# ======================================================================
# 8) get_active_summaries
# ======================================================================
def test_get_active_summaries_excludes_superseded(db_session, sample_character):
    """get_active_summaries 应只返回 is_active=1 的摘要"""
    active = MemorySummary(
        character_id=sample_character.id,
        summary_text="活跃摘要", msg_start_id=0, msg_end_id=5,
        msg_count=6, is_active=1, trigger_reason="adaptive",
    )
    superseded = MemorySummary(
        character_id=sample_character.id,
        summary_text="已被覆盖", msg_start_id=0, msg_end_id=3,
        msg_count=4, is_active=0, trigger_reason="adaptive",
    )
    db_session.add_all([active, superseded])
    db_session.commit()

    results = get_active_summaries(db_session, sample_character.id, limit=10)
    assert len(results) == 1
    assert results[0]["summary_text"] == "活跃摘要"
    assert results[0]["is_active"] if "is_active" in results[0] else True  # 仅 active 被返回


def test_get_active_summaries_empty(db_session, sample_character):
    """无摘要时返回空列表"""
    results = get_active_summaries(db_session, sample_character.id)
    assert results == []


def test_get_active_summaries_respects_limit(db_session, sample_character):
    """limit 应限制返回数量，并按 created_at 倒序"""
    for i in range(3):
        s = MemorySummary(
            character_id=sample_character.id,
            summary_text=f"摘要-{i}", msg_start_id=0, msg_end_id=i,
            msg_count=1, is_active=1, trigger_reason="adaptive",
        )
        s.created_at = datetime(2026, 6, 1) + timedelta(days=i)
        db_session.add(s)
    db_session.commit()

    results = get_active_summaries(db_session, sample_character.id, limit=2)
    assert len(results) == 2
    # 倒序 → 最新（摘要-2）在前
    assert results[0]["summary_text"] == "摘要-2"
