"""
主动消息模块（proactive.py）测试

覆盖场景：
  1. get_fallback_template: 硬编码模板（3×2 connection×pride 矩阵）
  2. get_fallback_template: 角色级 config 覆盖
  3. generate_proactive_content: LLM 成功返回 / 失败 fallback / 超时 fallback
  4. generate_and_store_proactive_message: 生成+入库+SSE 推送
  5. consume_and_insert: 消费主动消息写入 conversations
  6. reset_connection: 归零 connection 并落库
  7. get_proactive_messages: 队列查询过滤（unconsumed_only / limit / 角色隔离）
  8. push_proactive_message: SSE 推送到已连接客户端 + 失效客户端清理

注意：
  - conftest.py 提供 module 级 engine，db_session 是 autouse fixture（独立事务）
  - jiwen_manager 是全局单例，测试通过 monkeypatch 替换 _session_factory 实现隔离
  - LLM 调用全部 mock，不依赖真实 API
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from backend.models import (
    Character,
    ChatSession,
    Conversation,
    ProactiveMessage,
)


# ============================================================
# 测试用 helper
# ============================================================
def _create_proactive_message(db, character_id, content="主动消息内容", consumed=0):
    """创建主动消息并缓存 ID（detached 后仍可读）"""
    msg = ProactiveMessage(
        character_id=character_id,
        content=content,
        trigger_id=None,
        consumed=consumed,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg, msg.id


def _use_test_session(monkeypatch, db_session):
    """把全局 jiwen_manager 单例的 session_factory 替换为测试 session"""
    from backend.jiwen import get_jiwen_manager
    mgr = get_jiwen_manager()
    monkeypatch.setattr(mgr, "_session_factory", lambda: db_session)
    return mgr


def _make_safe_session_factory(db_session, monkeypatch):
    """
    创建安全的 session factory 用于 proactive 模块测试：
      - 用 begin_nested() 包裹 SAVEPOINT 隔离
      - 将 commit 替换为 flush（不真正提交外层事务）
      - SAVEPOINT 正常释放（flush 的数据在外层事务内可见）
      - 不在 with 退出时 close session（保持 fixture 可用）

    设计要点：
      generate_and_store_proactive_message 内部调用 db.commit()，
      但测试 fixture 的外层事务不应被提交（否则数据泄漏到其他测试）。
      通过将 commit→flush + SAVEPOINT 隔离，数据在测试内可见，
      测试结束后 fixture 的 trans.rollback() 统一清理。
    """
    def _fake_commit(self):
        self.flush()

    monkeypatch.setattr(type(db_session), "commit", _fake_commit)

    @contextmanager
    def _factory():
        # begin_nested 创建 SAVEPOINT，正常退出时 release（数据保留在外层事务中）
        with db_session.begin_nested():
            yield db_session

    return _factory


# ============================================================
# 1. get_fallback_template: 硬编码模板矩阵
# ============================================================
class TestGetFallbackTemplate:
    """测试 fallback 模板的 connection × pride 矩阵选择逻辑"""

    def test_high_connection_high_pride(self):
        """connection>=0.5 且 pride>=0.3 → 嘴硬语气"""
        from backend.modules.proactive import get_fallback_template
        result = get_fallback_template({"connection": 0.6, "pride": 0.4})
        assert "嘴硬" in result

    def test_high_connection_low_pride(self):
        """connection>=0.5 且 pride<0.3 → 温和语气"""
        from backend.modules.proactive import get_fallback_template
        result = get_fallback_template({"connection": 0.6, "pride": 0.1})
        assert "在忙吗" in result

    def test_mid_connection_high_pride(self):
        """0.35<=connection<0.5 且 pride>=0.3 → 犹豫语气"""
        from backend.modules.proactive import get_fallback_template
        result = get_fallback_template({"connection": 0.4, "pride": 0.5})
        assert "犹豫" in result

    def test_mid_connection_low_pride(self):
        """0.35<=connection<0.5 且 pride<0.3 → 日常问候"""
        from backend.modules.proactive import get_fallback_template
        result = get_fallback_template({"connection": 0.4, "pride": 0.1})
        assert "最近怎么样" in result

    def test_low_connection(self):
        """connection<0.35 → 基础问候"""
        from backend.modules.proactive import get_fallback_template
        result = get_fallback_template({"connection": 0.1, "pride": 0.0})
        assert "有空吗" in result

    def test_missing_state_fields_defaults_to_low(self):
        """缺少 connection/pride 字段时走默认分支（低 connection）"""
        from backend.modules.proactive import get_fallback_template
        result = get_fallback_template({})
        assert "有空吗" in result

    def test_boundary_connection_exactly_0_5(self):
        """边界: connection=0.5 应归入高档（>=0.5）"""
        from backend.modules.proactive import get_fallback_template
        result = get_fallback_template({"connection": 0.5, "pride": 0.4})
        assert "嘴硬" in result

    def test_boundary_connection_exactly_0_35(self):
        """边界: connection=0.35 应归入中档（>=0.35）"""
        from backend.modules.proactive import get_fallback_template
        result = get_fallback_template({"connection": 0.35, "pride": 0.1})
        assert "最近怎么样" in result


# ============================================================
# 2. get_fallback_template: 角色级 config 覆盖
# ============================================================
class TestFallbackTemplateCharacterOverride:
    """测试角色级 fallback_templates 配置覆盖"""

    def test_character_level_templates_used(self, db_session, sample_character):
        """角色 config.jiwen.fallback_templates 优先于硬编码模板"""
        from backend.modules.proactive import get_fallback_template

        char_id = sample_character.id
        sample_character.config = json.dumps({
            "jiwen": {
                "fallback_templates": ["自定义主动消息A", "自定义主动消息B"],
            }
        }, ensure_ascii=False)
        db_session.commit()

        sf = lambda: db_session
        result = get_fallback_template(
            {"connection": 0.6, "pride": 0.4},
            character_id=char_id,
            session_factory=sf,
        )
        assert result in ["自定义主动消息A", "自定义主动消息B"]

    def test_character_without_config_uses_default(self, db_session, sample_character):
        """角色无 config 时使用硬编码默认模板"""
        from backend.modules.proactive import get_fallback_template

        char_id = sample_character.id
        sf = lambda: db_session
        result = get_fallback_template(
            {"connection": 0.6, "pride": 0.4},
            character_id=char_id,
            session_factory=sf,
        )
        assert "嘴硬" in result

    def test_character_empty_templates_list_uses_default(self, db_session, sample_character):
        """角色 config 有 fallback_templates 但为空列表 → 走默认"""
        from backend.modules.proactive import get_fallback_template

        char_id = sample_character.id
        sample_character.config = json.dumps({
            "jiwen": {"fallback_templates": []},
        })
        db_session.commit()

        sf = lambda: db_session
        result = get_fallback_template(
            {"connection": 0.1, "pride": 0.0},
            character_id=char_id,
            session_factory=sf,
        )
        assert "有空吗" in result

    def test_nonexistent_character_id_uses_default(self, db_session):
        """角色 ID 不存在时走默认模板（不抛异常）"""
        from backend.modules.proactive import get_fallback_template

        sf = lambda: db_session
        result = get_fallback_template(
            {"connection": 0.6, "pride": 0.4},
            character_id=999999,
            session_factory=sf,
        )
        assert "嘴硬" in result


# ============================================================
# 3. generate_proactive_content: LLM 成功 + 失败 fallback
# ============================================================
class TestGenerateProactiveContent:
    """测试异步生成主动消息内容（LLM + fallback）"""

    def test_llm_success_returns_content(self, db_session, sample_character, monkeypatch):
        """LLM 正常返回时使用 LLM 内容（含空白清理）"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_proactive_content

        char_id = sample_character.id
        sample_character.soul_md = "温柔的高中语文老师"
        db_session.commit()

        mock_llm = MagicMock()
        mock_llm.call.return_value = "  今天月亮真好看，你有空看看吗？  "
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)

        sf = lambda: db_session
        content = asyncio.run(generate_proactive_content(
            char_id, {"connection": 0.5, "pride": 0.2, "reason": "想聊天"}, sf,
        ))

        assert "月亮" in content
        assert content == content.strip()  # 已清理首尾空白
        mock_llm.call.assert_called_once()

    def test_llm_returns_empty_falls_back(self, db_session, sample_character, monkeypatch):
        """LLM 返回空白字符串 → fallback 到模板"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_proactive_content

        char_id = sample_character.id
        mock_llm = MagicMock()
        mock_llm.call.return_value = "   "  # 空白
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)

        sf = lambda: db_session
        content = asyncio.run(generate_proactive_content(
            char_id, {"connection": 0.6, "pride": 0.4}, sf,
        ))
        assert "嘴硬" in content

    def test_llm_timeout_falls_back(self, db_session, sample_character, monkeypatch):
        """LLM 超时 → fallback 到模板"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_proactive_content

        char_id = sample_character.id

        async def fake_wait_for(coro, timeout=None):
            coro.close()  # 关闭协程避免未消费警告
            raise asyncio.TimeoutError()

        monkeypatch.setattr(proactive_mod.asyncio, "wait_for", fake_wait_for)

        mock_llm = MagicMock()
        mock_llm.call.return_value = "不该到这里"
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)

        sf = lambda: db_session
        content = asyncio.run(generate_proactive_content(
            char_id, {"connection": 0.1, "pride": 0.0}, sf,
        ))
        assert "有空吗" in content

    def test_llm_exception_falls_back(self, db_session, sample_character, monkeypatch):
        """LLM 抛异常 → fallback 到模板"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_proactive_content

        char_id = sample_character.id
        mock_llm = MagicMock()
        mock_llm.call.side_effect = RuntimeError("API 挂了")
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)

        sf = lambda: db_session
        content = asyncio.run(generate_proactive_content(
            char_id, {"connection": 0.4, "pride": 0.1}, sf,
        ))
        assert "最近怎么样" in content

    def test_character_not_found_falls_back(self, db_session, monkeypatch):
        """角色不存在 → fallback 到模板（不调用 LLM）"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_proactive_content

        mock_llm = MagicMock()
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)

        sf = lambda: db_session
        content = asyncio.run(generate_proactive_content(
            999999, {"connection": 0.6, "pride": 0.4}, sf,
        ))
        assert "嘴硬" in content
        mock_llm.call.assert_not_called()


# ============================================================
# 4. generate_and_store_proactive_message: 生成+入库+SSE
# ============================================================
class TestGenerateAndStoreProactiveMessage:
    """测试异步生成并存储主动消息（含 SSE 推送）"""

    def test_message_stored_in_db(self, db_session, sample_character, monkeypatch):
        """生成的主动消息应入库 proactive_messages 表"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_and_store_proactive_message
        import backend.api.jiwen_router as router_mod

        char_id = sample_character.id
        mock_llm = MagicMock()
        mock_llm.call.return_value = "LLM 生成的主动消息"
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)

        # 清空 SSE 客户端，避免推送副作用
        router_mod._sse_clients.clear()

        sf = _make_safe_session_factory(db_session, monkeypatch)
        asyncio.run(generate_and_store_proactive_message(
            character_id=char_id,
            trigger_state={"connection": 0.5, "pride": 0.2},
            trigger_id=42,
            session_factory=sf,
        ))

        # 验证入库（flush 后数据在同事务内可见）
        db_session.expire_all()
        msg = db_session.query(ProactiveMessage).filter(
            ProactiveMessage.character_id == char_id,
        ).first()
        assert msg is not None
        assert msg.content == "LLM 生成的主动消息"
        assert msg.trigger_id == 42
        assert msg.consumed == 0

    def test_fallback_content_stored_when_llm_fails(self, db_session, sample_character, monkeypatch):
        """LLM 失败时，fallback 模板内容应入库"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_and_store_proactive_message
        import backend.api.jiwen_router as router_mod

        char_id = sample_character.id
        mock_llm = MagicMock()
        mock_llm.call.side_effect = RuntimeError("API 挂了")
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)
        router_mod._sse_clients.clear()

        sf = _make_safe_session_factory(db_session, monkeypatch)
        asyncio.run(generate_and_store_proactive_message(
            character_id=char_id,
            trigger_state={"connection": 0.1, "pride": 0.0},
            trigger_id=1,
            session_factory=sf,
        ))

        db_session.expire_all()
        msg = db_session.query(ProactiveMessage).filter(
            ProactiveMessage.character_id == char_id,
        ).first()
        assert msg is not None
        assert "有空吗" in msg.content  # 低 connection 的 fallback

    def test_sse_push_called_when_clients_exist(self, db_session, sample_character, monkeypatch):
        """有 SSE 客户端时，push_proactive_message 应被调用"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_and_store_proactive_message
        import backend.api.jiwen_router as router_mod

        char_id = sample_character.id
        mock_llm = MagicMock()
        mock_llm.call.return_value = "推送测试"
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)

        # 模拟有 SSE 客户端
        push_called = {"v": False, "data": None}

        async def fake_push(data):
            push_called["v"] = True
            push_called["data"] = data

        monkeypatch.setattr(router_mod, "push_proactive_message", fake_push)
        # 必须在 jiwen_router 模块中设置 _sse_clients 非空
        router_mod._sse_clients.clear()
        router_mod._sse_clients[1] = {"queue": MagicMock()}

        sf = _make_safe_session_factory(db_session, monkeypatch)
        asyncio.run(generate_and_store_proactive_message(
            character_id=char_id,
            trigger_state={"connection": 0.5, "pride": 0.2},
            trigger_id=1,
            session_factory=sf,
        ))

        assert push_called["v"], "push_proactive_message 应被调用"
        assert push_called["data"]["character_id"] == char_id
        assert push_called["data"]["content"] == "推送测试"
        assert push_called["data"]["is_proactive"] is True

        # 清理
        router_mod._sse_clients.clear()

    def test_sse_push_failure_does_not_break_storage(self, db_session, sample_character, monkeypatch):
        """SSE 推送失败不应影响消息入库"""
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import generate_and_store_proactive_message
        import backend.api.jiwen_router as router_mod

        char_id = sample_character.id
        mock_llm = MagicMock()
        mock_llm.call.return_value = "推送失败测试"
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)

        async def failing_push(data):
            raise RuntimeError("SSE 推送失败")

        monkeypatch.setattr(router_mod, "push_proactive_message", failing_push)
        router_mod._sse_clients.clear()
        router_mod._sse_clients[1] = {"queue": MagicMock()}

        sf = _make_safe_session_factory(db_session, monkeypatch)
        # 不应抛异常
        asyncio.run(generate_and_store_proactive_message(
            character_id=char_id,
            trigger_state={"connection": 0.5, "pride": 0.2},
            trigger_id=1,
            session_factory=sf,
        ))

        # 消息仍应入库
        db_session.expire_all()
        msg = db_session.query(ProactiveMessage).filter(
            ProactiveMessage.character_id == char_id,
        ).first()
        assert msg is not None
        assert msg.content == "推送失败测试"

        router_mod._sse_clients.clear()


# ============================================================
# 5. consume_and_insert: 消费主动消息
# ============================================================
class TestConsumeAndInsert:
    """测试 consume_and_insert 消费主动消息写入 conversations"""

    def test_normal_consume_creates_session_and_conversation(self, db_session, sample_character, monkeypatch):
        """正常消费 → 创建 session + conversation，consumed=1"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        _, msg_id = _create_proactive_message(db_session, char_id, "你好呀")

        result = mgr.consume_and_insert(msg_id)

        assert result is not None
        assert result["character_id"] == char_id
        assert result["content"] == "你好呀"
        assert "session_id" in result
        assert "conversation_id" in result

        # DB 验证
        db_session.expire_all()
        msg = db_session.query(ProactiveMessage).filter(
            ProactiveMessage.id == msg_id
        ).first()
        assert msg.consumed == 1

        conv = db_session.query(Conversation).filter(
            Conversation.id == result["conversation_id"]
        ).first()
        assert conv is not None
        assert conv.is_proactive is True
        assert conv.npc_response == "你好呀"
        assert conv.user_input == ""

    def test_duplicate_consume_is_idempotent(self, db_session, sample_character, monkeypatch):
        """重复消费 → 幂等返回同一 conversation"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        _, msg_id = _create_proactive_message(db_session, char_id, "幂等测试")

        r1 = mgr.consume_and_insert(msg_id)
        r2 = mgr.consume_and_insert(msg_id)

        assert r1 is not None and r2 is not None
        assert r1["conversation_id"] == r2["conversation_id"]
        assert r1["session_id"] == r2["session_id"]

        db_session.expire_all()
        count = db_session.query(Conversation).filter(
            Conversation.npc_response == "幂等测试",
        ).count()
        assert count == 1

    def test_nonexistent_message_returns_none(self, db_session, sample_character, monkeypatch):
        """不存在的 message_id → 返回 None（不抛异常）"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)

        result = mgr.consume_and_insert(999999)
        assert result is None

    def test_consume_inserts_into_conversation_table(self, db_session, sample_character, monkeypatch):
        """消费后 conversations 表应有 is_proactive=True 的记录"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        _, msg_id = _create_proactive_message(db_session, char_id, "主动消息入库")

        mgr.consume_and_insert(msg_id)

        db_session.expire_all()
        conv = db_session.query(Conversation).filter(
            Conversation.character_id == char_id,
            Conversation.is_proactive == True,
        ).first()
        assert conv is not None
        assert conv.npc_response == "主动消息入库"
        assert conv.session_id is not None


# ============================================================
# 6. reset_connection: 归零 connection
# ============================================================
class TestResetConnection:
    """测试 reset_connection 行为"""

    def test_reset_zeroes_connection(self, db_session, sample_character, monkeypatch):
        """reset_connection 后 connection 应归零"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        # 先提升 connection
        mgr.apply_delta(char_id, {"connection": 0.7})
        state_before = mgr.get_state(char_id)
        assert state_before["connection"] > 0.5

        # 重置
        mgr.reset_connection(char_id)
        state_after = mgr.get_state(char_id)
        assert state_after["connection"] == 0.0

    def test_reset_persists_to_db(self, db_session, sample_character, monkeypatch):
        """reset_connection 后状态应落库（重新加载引擎仍为 0）"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        mgr.apply_delta(char_id, {"connection": 0.6})
        mgr.reset_connection(char_id)
        mgr.invalidate(char_id)  # 清缓存强制重读

        state = mgr.get_state(char_id)
        assert state["connection"] == 0.0

    def test_reset_does_not_affect_other_axes(self, db_session, sample_character, monkeypatch):
        """reset_connection 只归零 connection，不影响 pride/valence 等"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        mgr.apply_delta(char_id, {"connection": 0.7, "pride": 0.5, "valence": 0.3})
        mgr.reset_connection(char_id)

        state = mgr.get_state(char_id)
        assert state["connection"] == 0.0
        assert state["pride"] == pytest.approx(0.5, abs=1e-2)
        assert state["valence"] == pytest.approx(0.3, abs=1e-2)


# ============================================================
# 7. get_proactive_messages: 队列查询
# ============================================================
class TestGetProactiveMessages:
    """测试 proactive_messages 队列查询过滤"""

    def test_get_unconsumed_only(self, db_session, sample_character, monkeypatch):
        """unconsumed_only=True 只返回未消费消息"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        _create_proactive_message(db_session, char_id, "未消费1", consumed=0)
        _create_proactive_message(db_session, char_id, "未消费2", consumed=0)
        _create_proactive_message(db_session, char_id, "已消费", consumed=1)

        msgs = mgr.get_proactive_messages(char_id, unconsumed_only=True)
        contents = [m["content"] for m in msgs]
        assert "未消费1" in contents
        assert "未消费2" in contents
        assert "已消费" not in contents

    def test_get_all_including_consumed(self, db_session, sample_character, monkeypatch):
        """unconsumed_only=False 返回所有消息"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        _create_proactive_message(db_session, char_id, "消息A", consumed=0)
        _create_proactive_message(db_session, char_id, "消息B", consumed=1)

        msgs = mgr.get_proactive_messages(char_id, unconsumed_only=False)
        contents = [m["content"] for m in msgs]
        assert "消息A" in contents
        assert "消息B" in contents

    def test_get_respects_limit(self, db_session, sample_character, monkeypatch):
        """limit 参数限制返回数量"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)
        char_id = sample_character.id

        for i in range(5):
            _create_proactive_message(db_session, char_id, f"消息{i}", consumed=0)

        msgs = mgr.get_proactive_messages(char_id, limit=2, unconsumed_only=True)
        assert len(msgs) == 2

    def test_get_isolated_per_character(self, db_session, sample_character, monkeypatch):
        """不同角色的消息互不干扰"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)

        char1_id = sample_character.id
        char2 = Character(
            name="测试角色B",
            description="描述",
            world_setting="世界",
        )
        db_session.add(char2)
        db_session.commit()
        db_session.refresh(char2)
        char2_id = char2.id

        _create_proactive_message(db_session, char1_id, "角色1消息")
        _create_proactive_message(db_session, char2_id, "角色2消息")

        msgs1 = mgr.get_proactive_messages(char1_id)
        msgs2 = mgr.get_proactive_messages(char2_id)

        assert all(m["character_id"] == char1_id for m in msgs1)
        assert all(m["character_id"] == char2_id for m in msgs2)
        assert any(m["content"] == "角色1消息" for m in msgs1)
        assert any(m["content"] == "角色2消息" for m in msgs2)

    def test_get_returns_empty_for_character_with_no_messages(self, db_session, sample_character, monkeypatch):
        """无消息的角色返回空列表"""
        mgr = _use_test_session(monkeypatch, db_session)
        mgr.invalidate(sample_character.id)

        msgs = mgr.get_proactive_messages(sample_character.id)
        assert msgs == []


# ============================================================
# 8. push_proactive_message: SSE 推送机制
# ============================================================
class TestSSEPush:
    """测试 SSE 推送机制"""

    def test_push_with_no_clients_does_nothing(self, monkeypatch):
        """无客户端时推送为空操作（不抛异常）"""
        from backend.api.jiwen_router import push_proactive_message, _sse_clients

        _sse_clients.clear()
        asyncio.run(push_proactive_message({
            "character_id": 1,
            "content": "测试",
            "is_proactive": True,
        }))

    def test_push_delivers_to_connected_client(self, monkeypatch):
        """有客户端时推送应将事件放入队列"""
        from backend.api.jiwen_router import push_proactive_message, _sse_clients

        _sse_clients.clear()
        mock_queue = asyncio.Queue()
        _sse_clients[1] = {"queue": mock_queue}

        try:
            asyncio.run(push_proactive_message({
                "message_id": 100,
                "character_id": 1,
                "content": "SSE 推送内容",
                "is_proactive": True,
            }))

            assert not mock_queue.empty()
            event = mock_queue.get_nowait()
            assert "proactive_message" in event
            assert "SSE 推送内容" in event
            assert "event:" in event
        finally:
            _sse_clients.clear()

    def test_push_stale_client_gets_cleaned(self, monkeypatch):
        """失效客户端（put 超时/异常）应被清理"""
        from backend.api.jiwen_router import push_proactive_message, _sse_clients

        _sse_clients.clear()

        class BadQueue:
            async def put(self, item):
                raise asyncio.TimeoutError()

        _sse_clients[99] = {"queue": BadQueue()}

        try:
            asyncio.run(push_proactive_message({
                "character_id": 1,
                "content": "清理测试",
                "is_proactive": True,
            }))
            assert 99 not in _sse_clients
        finally:
            _sse_clients.clear()

    def test_push_event_format_correct(self, monkeypatch):
        """推送的事件格式应符合 SSE 规范"""
        from backend.api.jiwen_router import push_proactive_message, _sse_clients

        _sse_clients.clear()
        mock_queue = asyncio.Queue()
        _sse_clients[1] = {"queue": mock_queue}

        try:
            asyncio.run(push_proactive_message({
                "message_id": 1,
                "character_id": 2,
                "content": "格式测试",
                "is_proactive": True,
            }))

            event = mock_queue.get_nowait()
            # SSE 格式: event: <name>\ndata: <json>\n\n
            assert event.startswith("event: proactive_message\n")
            assert "data: " in event
            assert event.endswith("\n\n")
            # 解析 data JSON
            data_line = [l for l in event.split("\n") if l.startswith("data: ")][0]
            data = json.loads(data_line[len("data: "):])
            assert data["is_proactive"] is True
            assert data["content"] == "格式测试"
        finally:
            _sse_clients.clear()


# ============================================================
# 9. dispatch_proactive_message: 同步入口（集成）
# ============================================================
class TestDispatchProactiveMessage:
    """测试同步入口 dispatch_proactive_message"""

    def test_dispatch_does_not_block(self, db_session, sample_character, monkeypatch):
        """dispatch_proactive_message 应异步执行，不阻塞调用线程

        注意：后台事件循环在 daemon 线程中运行，会使用注入的 session_factory。
        由于 SQLAlchemy session 非线程安全，此处仅验证非阻塞行为，
        不验证后台任务的 DB 写入结果（后台任务异常会被 proactive 模块内部捕获）。
        """
        from backend.modules import proactive as proactive_mod
        from backend.modules.proactive import dispatch_proactive_message
        import backend.api.jiwen_router as router_mod

        char_id = sample_character.id
        mock_llm = MagicMock()
        mock_llm.call.return_value = "异步生成的内容"
        monkeypatch.setattr(proactive_mod, "LLMService", lambda: mock_llm)
        router_mod._sse_clients.clear()

        sf = _make_safe_session_factory(db_session, monkeypatch)

        # 同步调用，不应阻塞（后台任务在 daemon 线程执行）
        import time
        start = time.time()
        dispatch_proactive_message(
            character_id=char_id,
            trigger_state={"connection": 0.5, "pride": 0.2},
            trigger_id=1,
            session_factory=sf,
        )
        elapsed = time.time() - start
        assert elapsed < 2.0, "dispatch_proactive_message 不应阻塞调用线程"

    def test_dispatch_creates_background_loop(self, monkeypatch):
        """dispatch_proactive_message 应确保后台事件循环已启动"""
        from backend.modules.proactive import _get_proactive_loop, _proactive_loop

        # _get_proactive_loop 应返回一个运行中的事件循环
        loop = _get_proactive_loop()
        assert loop is not None
        assert loop.is_running()
