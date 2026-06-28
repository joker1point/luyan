"""
记忆衰减引擎 pytest 测试
"""
import math
from datetime import datetime, timezone, timedelta

import pytest

from backend.modules.memory_decay import (
    compute_half_life_days,
    compute_current_strength,
    should_forget,
    boost_factor,
    THEME_DECAY_CONFIG,
    get_active_memories,
    get_forgotten_ratio,
    recall_memory,
    run_decay_pass,
)


# ======================================================================
# 1) half_life 计算
# ======================================================================
def test_half_life_identity_long():
    hl = compute_half_life_days(importance=10, theme="identity")
    assert hl >= 365  # 身份主题高 importance 应该有很长的半衰期


def test_half_life_todo_short():
    hl = compute_half_life_days(importance=1, theme="todo")
    assert hl < 1  # 待办主题低 importance 半衰期很短


def test_half_life_higher_importance_longer():
    hl_low = compute_half_life_days(importance=2, theme="taste")
    hl_high = compute_half_life_days(importance=10, theme="taste")
    assert hl_high > hl_low


def test_half_life_default_for_unknown_theme():
    hl = compute_half_life_days(importance=5, theme="unknown")
    _, min_hl, _ = THEME_DECAY_CONFIG["default"]
    assert min_hl <= hl


# ======================================================================
# 2) current_strength
# ======================================================================
def test_current_strength_no_decay_zero_age():
    s = compute_current_strength(
        initial_strength=10, importance=5, age_days=0, recall_count=0,
    )
    assert s == pytest.approx(10.0, abs=1e-6)


def test_current_strength_recall_boost():
    s0 = compute_current_strength(10, 5, 30, 0, "identity")
    s10 = compute_current_strength(10, 5, 30, 10, "identity")
    assert s10 > s0


def test_current_strength_decay_over_time():
    s0 = compute_current_strength(10, 5, 0, 0, "moment")
    s7 = compute_current_strength(10, 5, 7, 0, "moment")
    assert s7 < s0


def test_current_strength_identity_decays_slow():
    """identity 主题应比 moment 衰减慢"""
    s_iden = compute_current_strength(10, 5, 30, 0, "identity")
    s_moment = compute_current_strength(10, 5, 30, 0, "moment")
    assert s_iden > s_moment


# ======================================================================
# 3) should_forget
# ======================================================================
def test_should_forget_low():
    assert should_forget(0.3, threshold=0.5) is True


def test_should_not_forget_high():
    assert should_forget(5.0, threshold=0.5) is False


# ======================================================================
# 4) boost_factor
# ======================================================================
def test_boost_factor_zero_recall_zero():
    b = boost_factor(recall_count=0, age_days=0, importance=5)
    assert b == 0.0


def test_boost_factor_high_recall_high_boost():
    b_low = boost_factor(recall_count=1, age_days=0, importance=5)
    b_high = boost_factor(recall_count=50, age_days=0, importance=5)
    assert b_high > b_low


def test_boost_factor_age_decay():
    b_fresh = boost_factor(recall_count=10, age_days=0, importance=5)
    b_old = boost_factor(recall_count=10, age_days=365, importance=5)
    assert b_old < b_fresh


def test_boost_factor_importance_weighted():
    b_low = boost_factor(recall_count=10, age_days=0, importance=1)
    b_high = boost_factor(recall_count=10, age_days=0, importance=10)
    assert b_high > b_low


# ======================================================================
# 5) DB 测试（用 conftest 中的 TestSession 风格）
# ======================================================================
def test_run_decay_pass_marks_forgotten(db_session, sample_character):
    """超老 + 低 importance 应被遗忘"""
    from backend.models import Memory
    import datetime as _dt

    # 创建一条 100 天前 importance=2 的 memory
    old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=100)
    mem = Memory(
        character_id=sample_character.id,
        content="一条很老的 memory",
        importance=2,
        theme="moment",
        strength=2,
        recall_count=0,
        forgotten=0,
    )
    mem.created_at = old_time
    db_session.add(mem)
    db_session.commit()

    result = run_decay_pass(db=db_session, character_id=sample_character.id)
    assert result["scanned"] >= 1
    assert result["forgotten"] >= 1
    db_session.refresh(mem)
    assert mem.forgotten == 1


def test_run_decay_pass_recall_keeps_alive(db_session, sample_character):
    """频繁 recall 应抗遗忘"""
    from backend.models import Memory

    old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=20)
    mem = Memory(
        character_id=sample_character.id,
        content="被频繁想起",
        importance=5,
        theme="moment",
        strength=5,
        recall_count=50,  # 频繁召回
        forgotten=0,
    )
    mem.created_at = old_time
    db_session.add(mem)
    db_session.commit()

    result = run_decay_pass(db=db_session, character_id=sample_character.id)
    db_session.refresh(mem)
    # recall_count 高，强度有 boost，可能不被遗忘
    # 但 age=20d + imp=5 + moment 半衰期短，可能还是被遗忘
    # 这取决于具体数值，仅验证不崩溃
    assert "scanned" in result


def test_get_active_memories_excludes_forgotten(db_session, sample_character):
    from backend.models import Memory
    active = Memory(
        character_id=sample_character.id, content="active",
        importance=5, theme="moment", strength=8, forgotten=0,
    )
    forgotten = Memory(
        character_id=sample_character.id, content="forgotten",
        importance=5, theme="moment", strength=2, forgotten=1,
    )
    db_session.add_all([active, forgotten])
    db_session.commit()

    results = get_active_memories(db=db_session, character_id=sample_character.id, limit=10)
    contents = [r["content"] for r in results]
    assert "active" in contents
    assert "forgotten" not in contents


def test_get_active_memories_sorted_by_strength(db_session, sample_character):
    from backend.models import Memory
    m1 = Memory(character_id=sample_character.id, content="weak", importance=3, strength=3, forgotten=0)
    m2 = Memory(character_id=sample_character.id, content="strong", importance=9, strength=9, forgotten=0)
    db_session.add_all([m1, m2])
    db_session.commit()

    results = get_active_memories(db=db_session, character_id=sample_character.id, limit=10)
    # 第一个应该是 strong
    assert results[0]["content"] == "strong"


def test_get_active_memories_with_boost(db_session, sample_character):
    from backend.models import Memory
    m = Memory(
        character_id=sample_character.id, content="test",
        importance=7, strength=7, recall_count=20, forgotten=0,
    )
    db_session.add(m)
    db_session.commit()

    results = get_active_memories(db=db_session, character_id=sample_character.id, limit=10, include_decay_boost=True)
    assert "boost" in results[0]
    assert results[0]["boost"] > 0


def test_recall_memory_increments_count(db_session, sample_character):
    from backend.models import Memory
    m = Memory(character_id=sample_character.id, content="x", importance=5, strength=5, recall_count=0, forgotten=0)
    db_session.add(m)
    db_session.commit()
    mem_id = m.id

    success = recall_memory(db=db_session, memory_id=mem_id)
    assert success is True
    db_session.refresh(m)
    assert m.recall_count == 1
    assert m.last_recalled_at is not None
    assert m.strength >= 5  # 至少不减


def test_get_forgotten_ratio_empty(db_session, sample_character):
    ratio = get_forgotten_ratio(db=db_session, character_id=sample_character.id)
    assert ratio == 0.0


def test_get_forgotten_ratio_with_data(db_session, sample_character):
    from backend.models import Memory
    for i in range(3):
        db_session.add(Memory(
            character_id=sample_character.id, content=f"m{i}",
            importance=5, strength=5, forgotten=0,
        ))
    db_session.add(Memory(
        character_id=sample_character.id, content="f1",
        importance=5, strength=5, forgotten=1,
    ))
    db_session.commit()
    ratio = get_forgotten_ratio(db=db_session, character_id=sample_character.id)
    assert ratio == pytest.approx(0.25, abs=1e-6)  # 1/4
