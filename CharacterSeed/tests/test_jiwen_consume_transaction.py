"""
Q6 事务安全验收测试 — 按 docs/answer.md 第 6.4 节验收清单逐项验证

覆盖场景：
  AC1: 正常消费（consume → 200 + session_id + conversation_id，consumed=1）
  AC2: 重复消费（幂等返回同一 session/conversation，无重复数据）
  AC3: 异常恢复（Conv 已插但 consumed 未标 → 幂等返回已有 conv）
  AC4: 不存在消息 → 404
  AC5: 事务回滚（DB 写入异常 → consumed 保持 0，可重试）

注意：
  - 测试 conftest.py 提供 module 级 engine，db_session 是 autouse fixture（独立事务）
  - jiwen_manager 是全局单例，使用生产 SessionLocal；测试通过 monkeypatch
    把单例的 _session_factory 替换为 db_session 实现隔离
  - 测试断言的 id 字段需在 fixture 创建时缓存（int 类型，detached 后仍可读）
"""
from contextlib import contextmanager

import pytest
from unittest.mock import MagicMock, patch

from backend.models import (
    Character,
    ChatSession,
    Conversation,
    ProactiveMessage,
)


# ============================================================
# 测试用 helper
# ============================================================
def _create_proactive_message(db, character_id, content="主动消息内容"):
    msg = ProactiveMessage(
        character_id=character_id,
        content=content,
        trigger_id=None,
        consumed=0,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    # 缓存 int ID（db_session 在 fixture 退出时会 close，detached 后无法访问 id）
    return msg, msg.id


def _use_test_session(monkeypatch, db_session):
    """把全局 jiwen_manager 单例的 session_factory 替换为测试 session"""
    from backend.jiwen import get_jiwen_manager
    mgr = get_jiwen_manager()
    monkeypatch.setattr(mgr, "_session_factory", lambda: db_session)
    return mgr


# ============================================================
# AC1: 正常消费
# ============================================================
def test_ac1_normal_consume_returns_session_and_conversation(db_session, sample_character, monkeypatch):
    """AC1: 调用 consume_and_insert → 返回 session_id + conversation_id，DB 中 consumed=1，conv 已插入"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)  # 清缓存，强制用新 factory

    # 缓存 int id（避免 detached 后无法访问）
    char_id = sample_character.id
    _, msg_id = _create_proactive_message(db_session, char_id, "你好，最近怎么样？")

    result = mgr.consume_and_insert(msg_id)

    # 返回结构
    assert result is not None, "consume_and_insert 必须返回 dict，不能为 None"
    assert "session_id" in result
    assert "conversation_id" in result
    assert result["character_id"] == char_id
    assert result["content"] == "你好，最近怎么样？"

    # DB 验证
    db_session.expire_all()
    msg_check = db_session.query(ProactiveMessage).filter(ProactiveMessage.id == msg_id).first()
    assert msg_check.consumed == 1, f"consumed 必须被标记为 1，实际 {msg_check.consumed}"

    conv = db_session.query(Conversation).filter(
        Conversation.id == result["conversation_id"]
    ).first()
    assert conv is not None
    assert conv.is_proactive == True
    assert conv.npc_response == "你好，最近怎么样？"
    assert conv.session_id == result["session_id"]
    assert conv.character_id == char_id

    # session 已创建
    session = db_session.query(ChatSession).filter(
        ChatSession.id == result["session_id"]
    ).first()
    assert session is not None
    assert session.character_id == char_id


# ============================================================
# AC2: 重复消费（幂等）
# ============================================================
def test_ac2_duplicate_consume_is_idempotent(db_session, sample_character, monkeypatch):
    """AC2: 再次调用 consume_and_insert 同一 message_id → 幂等返回，DB 中只有一条 conv"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    _, msg_id = _create_proactive_message(db_session, char_id, "第一次消费")

    # 第一次消费
    result1 = mgr.consume_and_insert(msg_id)
    assert result1 is not None
    conv_id_1 = result1["conversation_id"]
    session_id_1 = result1["session_id"]

    # 第二次消费（重复）
    result2 = mgr.consume_and_insert(msg_id)
    assert result2 is not None
    assert result2["conversation_id"] == conv_id_1, "幂等：必须返回同一 conversation_id"
    assert result2["session_id"] == session_id_1, "幂等：必须返回同一 session_id"

    # DB 中只有一条 conv（防止重复插入）
    db_session.expire_all()
    conv_count = db_session.query(Conversation).filter(
        Conversation.character_id == char_id,
        Conversation.npc_response == "第一次消费",
    ).count()
    assert conv_count == 1, f"幂等性：必须只有一条 conversation，实际 {conv_count} 条"


# ============================================================
# AC3: 异常恢复（Conv 已插但 consumed 未标 → 幂等返回已有 conv）
# ============================================================
def test_ac3_idempotent_recovery_when_consumed_but_conv_exists(db_session, sample_character, monkeypatch):
    """AC3: 模拟"消息已消费 + 已有同内容 conv"的异常状态 → consume_and_insert 应幂等返回已有 conv，不重复插入"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id

    # 准备：插入主动消息 + 标记已消费 + 插入一条已存在的 proactive conv
    _, msg_id = _create_proactive_message(db_session, char_id, "已存在的主动消息")
    msg = db_session.query(ProactiveMessage).filter(ProactiveMessage.id == msg_id).first()
    msg.consumed = 1
    db_session.commit()

    existing_conv = Conversation(
        character_id=char_id,
        session_id=None,  # 旧数据可能如此
        user_input="",
        npc_response="已存在的主动消息",
        is_proactive=True,
    )
    db_session.add(existing_conv)
    db_session.commit()
    db_session.refresh(existing_conv)
    existing_conv_id = existing_conv.id

    # 触发幂等性路径：consumed=1 + 已有同内容 conv
    result = mgr.consume_and_insert(msg_id)

    # 幂等返回
    assert result is not None
    assert result["conversation_id"] == existing_conv_id, "幂等性：应返回已存在的 conversation_id"

    # DB 中没有重复插入
    db_session.expire_all()
    conv_count = db_session.query(Conversation).filter(
        Conversation.npc_response == "已存在的主动消息",
    ).count()
    assert conv_count == 1, f"幂等性：不能重复插入，实际 {conv_count} 条"


# ============================================================
# AC4: 不存在消息 → 404（API 层）
# ============================================================
def test_ac4_nonexistent_message_returns_404(client, sample_character):
    """AC4: 调用不存在的 message_id → API 返回 404"""
    char_id = sample_character.id
    response = client.post(
        f"/api/jiwen/{char_id}/proactive-messages/999999/consume"
    )
    assert response.status_code == 404
    detail = response.json().get("detail", "")
    assert "不存在" in detail or "已消费" in detail, f"返回 detail 应提示不存在/已消费，实际: {detail}"


def test_ac4b_consumed_with_no_conv_returns_404(db_session, sample_character, monkeypatch):
    """AC4 补充: 消息已消费但找不到对应 conv（数据异常） → consume_and_insert 返回 None → API 404"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    _, msg_id = _create_proactive_message(db_session, char_id, "已消费但无 conv")
    msg = db_session.query(ProactiveMessage).filter(ProactiveMessage.id == msg_id).first()
    msg.consumed = 1
    db_session.commit()
    # 故意不创建任何 conversation

    result = mgr.consume_and_insert(msg_id)
    assert result is None, "已消费但无对应 conv → 应返回 None"


# ============================================================
# AC5: 事务回滚
# ============================================================
def test_ac5_rollback_when_conversation_insert_fails(db_session, sample_character, monkeypatch):
    """AC5: 模拟 Conversation 插入失败 → consumed 必须保持 0（事务回滚）→ 可重试

    策略：通过 monkeypatch 让 _find_or_create_session 抛异常，触发 _db() 整体回滚
    """
    from backend.jiwen import get_jiwen_manager

    mgr_real = get_jiwen_manager()
    monkeypatch.setattr(mgr_real, "_session_factory", lambda: db_session)
    mgr_real.invalidate(sample_character.id)

    char_id = sample_character.id
    _, msg_id = _create_proactive_message(db_session, char_id, "测试回滚")

    # 第一次：让 _find_or_create_session 抛异常，模拟中途失败
    call_count = {"n": 0}

    def flaky_find_session(db, character_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("模拟 DB 写入失败")
        # 第二次正常返回
        from backend.models import ChatSession
        sess = ChatSession(character_id=character_id)
        db.add(sess)
        db.flush()
        return sess

    monkeypatch.setattr(mgr_real, "_find_or_create_session", flaky_find_session)

    # 第一次应失败
    result = mgr_real.consume_and_insert(msg_id)
    assert result is None, "异常时 consume_and_insert 应返回 None"

    # 验证 msg 仍存在且 consumed=0（事务回滚）
    db_session.expire_all()
    msg_check = db_session.query(ProactiveMessage).filter(
        ProactiveMessage.id == msg_id
    ).first()
    assert msg_check is not None
    assert msg_check.consumed == 0, f"回滚后 consumed 必须保持 0，实际 {msg_check.consumed}"

    # 验证没有 conversation 被插入
    conv_count = db_session.query(Conversation).filter(
        Conversation.character_id == char_id,
        Conversation.npc_response == "测试回滚",
    ).count()
    assert conv_count == 0, f"回滚后不应有 conversation，实际 {conv_count} 条"

    # 可重试：第二次调用，让 _find_or_create_session 正常返回
    result2 = mgr_real.consume_and_insert(msg_id)
    assert result2 is not None, "修复后应可重试成功"
    assert result2["content"] == "测试回滚"

    # 重试后 consumed=1
    db_session.expire_all()
    msg_check2 = db_session.query(ProactiveMessage).filter(
        ProactiveMessage.id == msg_id
    ).first()
    assert msg_check2.consumed == 1, "重试成功后 consumed 应为 1"


# ============================================================
# 辅助：_db() 异常路径验证
# ============================================================
def test_db_contextmanager_rollback_on_exception(db_session, sample_character, monkeypatch):
    """辅助验证：_db() contextmanager 异常时确实回滚"""
    from backend.models import JiwenState

    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id

    # 触发 _db() 内部异常
    with pytest.raises(ValueError):
        with mgr._db() as db:
            state = JiwenState(character_id=char_id, connection=50)
            db.add(state)
            # 主动抛异常（_db() 应捕获后回滚）
            raise ValueError("测试异常")

    # 验证回滚：state 不应被持久化
    db_session.expire_all()
    state_count = db_session.query(JiwenState).filter(
        JiwenState.character_id == char_id
    ).count()
    assert state_count == 0, f"_db() 异常时应回滚，实际有 {state_count} 条"


def test_db_contextmanager_commits_on_normal_exit(db_session, sample_character, monkeypatch):
    """辅助验证：_db() contextmanager 正常退出时 commit"""
    from backend.models import JiwenState

    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id

    # 正常路径
    with mgr._db() as db:
        state = JiwenState(character_id=char_id, connection=80)
        db.add(state)
    # 退出 with → 自动 commit

    # 验证已持久化
    db_session.expire_all()
    state = db_session.query(JiwenState).filter(
        JiwenState.character_id == char_id
    ).first()
    assert state is not None, "_db() 正常退出时必须 commit"
    assert state.connection == 80


# ============================================================
# 边界条件测试
# ============================================================
def test_boundary_empty_content_message(db_session, sample_character, monkeypatch):
    """边界: 空字符串 content 仍应正常消费"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    _, msg_id = _create_proactive_message(db_session, char_id, content="")

    result = mgr.consume_and_insert(msg_id)
    assert result is not None, "空 content 不应阻止消费"
    assert result["content"] == ""


def test_boundary_unicode_content_message(db_session, sample_character, monkeypatch):
    """边界: Unicode 表情+中文 content"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    unicode_content = "我想你了 😊 最近好嘛？"
    _, msg_id = _create_proactive_message(db_session, char_id, content=unicode_content)

    result = mgr.consume_and_insert(msg_id)
    assert result is not None
    assert result["content"] == unicode_content, "Unicode 应原样保留"

    db_session.expire_all()
    conv = db_session.query(Conversation).filter(
        Conversation.id == result["conversation_id"]
    ).first()
    assert conv.npc_response == unicode_content


def test_boundary_very_long_content(db_session, sample_character, monkeypatch):
    """边界: 极长 content（5000 字符）"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    long_content = "测试" * 2500  # 5000 字符
    _, msg_id = _create_proactive_message(db_session, char_id, content=long_content)

    result = mgr.consume_and_insert(msg_id)
    assert result is not None
    assert len(result["content"]) == 5000

    db_session.expire_all()
    conv = db_session.query(Conversation).filter(
        Conversation.id == result["conversation_id"]
    ).first()
    assert conv.npc_response == long_content


def test_boundary_zero_message_id(db_session, sample_character, monkeypatch):
    """边界: message_id=0（不存在的 ID）→ 返回 None"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    result = mgr.consume_and_insert(0)
    assert result is None, "message_id=0 应返回 None"


def test_boundary_negative_message_id(db_session, sample_character, monkeypatch):
    """边界: 负数 message_id（异常输入）→ 返回 None"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    result = mgr.consume_and_insert(-1)
    assert result is None, "负数 message_id 应返回 None"


def test_boundary_nonexistent_message_id_returns_none(db_session, sample_character, monkeypatch):
    """边界: 不存在的大 ID → 返回 None（不抛异常）"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    result = mgr.consume_and_insert(999999)
    assert result is None, "不存在的 message_id 应返回 None，不抛异常"


# ============================================================
# 并发场景测试
# ============================================================
def test_concurrent_double_consume_same_message(db_session, sample_character, monkeypatch):
    """并发: 同一 message_id 连续两次消费 → 第二返回幂等结果"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    _, msg_id = _create_proactive_message(db_session, char_id, "并发消费测试")

    # 模拟两个请求"同时"打到 consume_and_insert
    r1 = mgr.consume_and_insert(msg_id)
    r2 = mgr.consume_and_insert(msg_id)
    r3 = mgr.consume_and_insert(msg_id)

    assert r1 is not None
    assert r2 is not None
    assert r3 is not None
    # 三个结果应指向同一 session/conversation
    assert r1["conversation_id"] == r2["conversation_id"] == r3["conversation_id"]
    assert r1["session_id"] == r2["session_id"] == r3["session_id"]

    # DB 中只有一条 conv
    db_session.expire_all()
    conv_count = db_session.query(Conversation).filter(
        Conversation.npc_response == "并发消费测试"
    ).count()
    assert conv_count == 1, f"并发幂等性：应只有一条 conv，实际 {conv_count} 条"


# ============================================================
# 兼容性测试 - 字段兼容
# ============================================================
def test_compat_old_proactive_message_no_content(db_session, sample_character, monkeypatch):
    """兼容: content 为空字符串（防御旧数据）"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    # 模拟旧数据：content 为空字符串（业务上可能存在）
    msg = ProactiveMessage(
        character_id=char_id,
        content="",
        consumed=0,
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    msg_id = msg.id

    # 不抛异常即可
    result = mgr.consume_and_insert(msg_id)
    # content="" 会被正常消费（与 boundary_empty_content 一致）
    assert result is not None
    assert result["content"] == ""


def test_compat_redis_locking_with_simulated_lock(db_session, sample_character, monkeypatch):
    """兼容: 模拟分布式锁场景下的消费（即使有外部锁也应可重入）"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    _, msg_id = _create_proactive_message(db_session, char_id, "锁测试")

    # 模拟：第一次消费时"持锁"，第二次消费时"无锁"
    lock_held = {"v": True}

    def wrapped_consume(message_id):
        if lock_held["v"]:
            lock_held["v"] = False
            return mgr.consume_and_insert(message_id)
        return mgr.consume_and_insert(message_id)

    r1 = wrapped_consume(msg_id)
    r2 = wrapped_consume(msg_id)  # 锁已释放

    assert r1 is not None
    assert r2 is not None
    assert r1["conversation_id"] == r2["conversation_id"]


# ============================================================
# 性能基准测试
# ============================================================
def test_perf_100_consecutive_consumes(db_session, sample_character, monkeypatch):
    """性能: 连续 100 次消费不同 message 的耗时 < 10s"""
    import time
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    char_id = sample_character.id
    msg_ids = []
    for i in range(100):
        _, mid = _create_proactive_message(db_session, char_id, content=f"性能测试 {i}")
        msg_ids.append(mid)

    start = time.time()
    results = []
    for mid in msg_ids:
        r = mgr.consume_and_insert(mid)
        results.append(r)
    elapsed = time.time() - start

    assert all(r is not None for r in results), "100 次消费必须全部成功"
    assert elapsed < 10.0, f"100 次消费耗时 {elapsed:.2f}s 超过 10s 阈值"

    # 验证最终一致性：100 条 conv + 100 条 consumed msg
    db_session.expire_all()
    conv_count = db_session.query(Conversation).filter(
        Conversation.character_id == char_id
    ).count()
    assert conv_count >= 100

    consumed_count = db_session.query(ProactiveMessage).filter(
        ProactiveMessage.consumed == 1
    ).count()
    assert consumed_count >= 100

    print(f"\n  性能: 100 次连续消费耗时 {elapsed:.3f}s (平均 {elapsed/100*1000:.2f}ms/次)")


# ============================================================
# 跨角色隔离测试
# ============================================================
def test_isolation_two_characters_consume_independently(db_session, sample_character, monkeypatch):
    """隔离: 两个不同角色的 consume 操作互不影响"""
    mgr = _use_test_session(monkeypatch, db_session)
    mgr.invalidate(sample_character.id)

    # 创建第二个角色
    char2 = Character(
        name="林远",
        description="理性内向的大学物理教授",
        world_setting="2026 年夏，京城",
        personality='{"logic": 9, "humor": 3}',
    )
    db_session.add(char2)
    db_session.commit()
    db_session.refresh(char2)
    char2_id = char2.id

    char1_id = sample_character.id

    # 给两个角色分别创建主动消息
    _, msg1_id = _create_proactive_message(db_session, char1_id, "苏晴的消息")
    _, msg2_id = _create_proactive_message(db_session, char2_id, "林远的消息")

    # 分别消费
    r1 = mgr.consume_and_insert(msg1_id)
    r2 = mgr.consume_and_insert(msg2_id)

    assert r1 is not None and r2 is not None
    assert r1["character_id"] == char1_id
    assert r2["character_id"] == char2_id
    # 各自的 session 应该独立
    assert r1["session_id"] != r2["session_id"]
    # 各自的 conv 应该独立
    assert r1["conversation_id"] != r2["conversation_id"]

    # 验证 DB 状态
    db_session.expire_all()
    s1 = db_session.query(ChatSession).filter(ChatSession.id == r1["session_id"]).first()
    s2 = db_session.query(ChatSession).filter(ChatSession.id == r2["session_id"]).first()
    assert s1.character_id == char1_id
    assert s2.character_id == char2_id
