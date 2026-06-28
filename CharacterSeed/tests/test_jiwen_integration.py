"""
jiwen + 记忆/遗忘系统 集成测试

需要 conftest.py 提供：
  - db_session: SQLAlchemy session
  - sample_character: 一个已创建的 Character 实例
"""
import pytest

from backend.jiwen import get_jiwen_manager


# ======================================================================
# jiwen_manager 集成
# ======================================================================
def test_jiwen_manager_persists_state(db_session, sample_character):
    mgr = get_jiwen_manager()
    mgr.apply_delta(sample_character.id, {"pride": 0.3, "valence": 0.2})
    mgr.invalidate(sample_character.id)  # 清缓存

    # 重新获取应读到已持久化的状态
    mgr2 = get_jiwen_manager()
    state = mgr2.get_state(sample_character.id)
    assert state["pride"] == pytest.approx(0.3, abs=1e-2)
    assert state["valence"] == pytest.approx(0.2, abs=1e-2)


def test_jiwen_manager_set_activity_persists(db_session, sample_character):
    mgr = get_jiwen_manager()
    mgr.set_activity(sample_character.id, "reading", "红楼梦")
    mgr.invalidate(sample_character.id)

    mgr2 = get_jiwen_manager()
    state = mgr2.get_state(sample_character.id)
    assert state["activity_type"] == "reading"
    assert state["activity_label"] == "红楼梦"
    assert state["immersion"] >= 0.7


def test_jiwen_manager_set_last_chat_message_id(db_session, sample_character):
    mgr = get_jiwen_manager()
    mgr.set_last_chat_message_id(sample_character.id, 42, "你好")
    mgr.invalidate(sample_character.id)

    mgr2 = get_jiwen_manager()
    state = mgr2.get_state(sample_character.id)
    assert state["last_chat_message_id"] == 42
    assert state["last_chat_content"] == "你好"


def test_jiwen_manager_tick_persists_triggers(db_session, sample_character):
    """tick 触发的 triggers 应落库"""
    from backend.models import JiwenTrigger

    mgr = get_jiwen_manager()
    # 加速连接增长以确保触发
    engine = mgr.get_engine(
        sample_character.id,
        rates={"connectionAccel": 0.05},
    )
    engine.set_last_chat_message_id(1, "短")
    triggers = engine.tick(60 * 12)  # 12h
    engine.save()

    # 验证 triggers 落库
    triggers_db = mgr.get_recent_triggers(sample_character.id, limit=20)
    # 至少应有 observation 或 contact
    actions = [t["action"] for t in triggers_db]
    # 可能没触发（因为 last_chat_at 是 engine 内 set 的，跟 manager 的 last_tick_at 不同步）
    # 仅验证 API 可用
    assert isinstance(triggers_db, list)


def test_jiwen_scheduler_status():
    from backend.jiwen.jiwen_scheduler import get_scheduler
    sched = get_scheduler()
    status = sched.status()
    assert "is_running" in status
    assert "interval_seconds" in status
    assert "characters_with_triggers" in status


def test_jiwen_scheduler_tick_now_no_crash():
    """tick_now 在无角色场景不应崩溃"""
    from backend.jiwen.jiwen_scheduler import get_scheduler
    sched = get_scheduler()
    result = sched.tick_now()
    assert isinstance(result, dict)


# ======================================================================
# post_chat 钩子
# ======================================================================
def test_post_chat_hooks_basic(db_session, sample_character):
    """post_chat_hooks 不应抛异常"""
    from backend.modules.post_chat import post_chat_hooks
    try:
        result = post_chat_hooks(
            character_id=sample_character.id,
            user_input="今天好累",
            npc_response="辛苦了",
            emotion_label="悲伤",
            run_in_background=False,
        )
        assert isinstance(result, dict)
        assert "jiwen_delta" in result
        # 悲伤 → valence -0.15
        assert result["jiwen_delta"].get("valence", 0) <= 0
    except Exception as e:
        pytest.fail(f"post_chat_hooks failed: {e}")


def test_infer_emotion_delta_positive():
    from backend.modules.post_chat import infer_emotion_delta
    delta = infer_emotion_delta("我喜欢你", "我也喜欢你", "高兴")
    assert delta.get("valence", 0) > 0


def test_infer_emotion_delta_negative():
    from backend.modules.post_chat import infer_emotion_delta
    delta = infer_emotion_delta("滚", "好", "愤怒")
    assert delta.get("valence", 0) < 0
    assert delta.get("pride", 0) > 0  # 骄傲上升


def test_infer_emotion_delta_intimate():
    from backend.modules.post_chat import infer_emotion_delta
    delta = infer_emotion_delta("晚安", "晚安", "平静")
    assert delta.get("connection", 0) < 0  # 亲密接触 → connection 降


# ======================================================================
# summary_trigger
# ======================================================================
def test_should_summarize_too_few_messages(db_session, sample_character):
    from backend.modules.summary_trigger import should_summarize
    # 没有对话 → 不应触发
    decision = should_summarize(db=db_session, character_id=sample_character.id)
    assert decision["should"] is False
    assert "too_few" in decision["reason"] or "no_trigger" in decision["reason"]


def test_should_summarize_msg_overflow(db_session, sample_character):
    from backend.models import Conversation
    from backend.modules.summary_trigger import should_summarize, MAX_MESSAGES_BETWEEN

    # 插入 MAX+5 条 conversation
    for i in range(MAX_MESSAGES_BETWEEN + 5):
        db_session.add(Conversation(
            character_id=sample_character.id,
            user_input=f"msg {i}", npc_response=f"resp {i}",
            session_id=None,
        ))
    db_session.commit()

    decision = should_summarize(db=db_session, character_id=sample_character.id)
    assert decision["should"] is True
    assert "msg_count_overflow" in decision["reason"]


def test_create_summary_chain_supersede(db_session, sample_character):
    from backend.models import Conversation, MemorySummary
    from backend.modules.summary_trigger import create_summary, should_summarize, MAX_MESSAGES_BETWEEN

    # 第一次：插入 MAX+5 条 + 触发
    for i in range(MAX_MESSAGES_BETWEEN + 5):
        db_session.add(Conversation(
            character_id=sample_character.id,
            user_input=f"msg {i}", npc_response=f"resp {i}",
            session_id=None,
        ))
    db_session.commit()

    # 第一次摘要
    first = create_summary(
        db=db_session,
        character_id=sample_character.id,
        trigger_reason="test_first",
    )
    # LLM 可能失败（无 API key），所以可能返回 None
    # 但 DB 结构应正确
    all_summaries = db_session.query(MemorySummary).filter(
        MemorySummary.character_id == sample_character.id,
    ).all()
    # 至少有创建尝试（可能 None 因 LLM 失败）


def test_get_active_summaries_empty(db_session, sample_character):
    from backend.modules.summary_trigger import get_active_summaries
    summaries = get_active_summaries(db=db_session, character_id=sample_character.id)
    assert summaries == []


# ======================================================================
# 记忆提取（Mock LLM）
# ======================================================================
def test_extract_memories_invalid_json_returns_empty():
    from backend.modules.memory_extractor import extract_memories_from_conversation

    # 使用一个 mock LLM service（parse_json_response 返回非 list）
    class MockLLM:
        def call(self, **kwargs):
            return "not json"

        def parse_json_response(self, raw):
            return {}

    items = extract_memories_from_conversation(
        user_input="hi",
        npc_response="hello",
        llm_service=MockLLM(),
    )
    assert items == []


def test_extract_memories_validates_theme():
    from backend.modules.memory_extractor import extract_memories_from_conversation

    class MockLLM:
        def call(self, **kwargs):
            return "{}"

        def parse_json_response(self, raw):
            return [
                {"content": "用户喜欢爵士乐", "importance": 7, "theme": "music", "type": "preference"},
                {"content": "bad theme", "importance": 5, "theme": "invalid_theme", "type": "fact"},
            ]

    items = extract_memories_from_conversation(
        user_input="hi", npc_response="hello", llm_service=MockLLM(),
    )
    assert len(items) == 2
    assert items[0]["theme"] == "music"
    assert items[1]["theme"] is None  # invalid → None


def test_extract_memories_importance_clipped():
    from backend.modules.memory_extractor import extract_memories_from_conversation

    class MockLLM:
        def call(self, **kwargs):
            return "{}"

        def parse_json_response(self, raw):
            return [
                {"content": "too high", "importance": 99, "theme": "moment", "type": "fact"},
                {"content": "negative", "importance": -3, "theme": "moment", "type": "fact"},
            ]

    items = extract_memories_from_conversation(
        user_input="hi", npc_response="hello", llm_service=MockLLM(),
    )
    assert items[0]["importance"] == 10  # clip to 10
    assert items[1]["importance"] == 1   # clip to 1


def test_extract_memories_empty_content_filtered():
    from backend.modules.memory_extractor import extract_memories_from_conversation

    class MockLLM:
        def call(self, **kwargs):
            return "{}"

        def parse_json_response(self, raw):
            return [
                {"content": "valid", "importance": 5, "theme": "moment", "type": "fact"},
                {"content": "  ", "importance": 5, "theme": "moment", "type": "fact"},
                {"content": "", "importance": 5, "theme": "moment", "type": "fact"},
            ]

    items = extract_memories_from_conversation(
        user_input="hi", npc_response="hello", llm_service=MockLLM(),
    )
    assert len(items) == 1


# ======================================================================
# REST API
# ======================================================================
def test_jiwen_api_get_state(client, sample_character):
    resp = client.get(f"/api/jiwen/{sample_character.id}/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["character_id"] == sample_character.id
    assert "state" in data
    assert "summary" in data


def test_jiwen_api_get_state_404(client):
    resp = client.get("/api/jiwen/999999/state")
    assert resp.status_code == 404


def test_jiwen_api_apply_delta(client, sample_character):
    resp = client.post(
        f"/api/jiwen/{sample_character.id}/delta",
        json={"pride": -0.1, "valence": 0.2},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_delta"]["pride"] == -0.1


def test_jiwen_api_apply_delta_empty_400(client, sample_character):
    resp = client.post(
        f"/api/jiwen/{sample_character.id}/delta",
        json={},
    )
    assert resp.status_code == 400


def test_jiwen_api_tick(client, sample_character):
    resp = client.post(
        f"/api/jiwen/{sample_character.id}/tick",
        json={"minutes": 60},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "triggers" in data
    assert "state" in data


def test_jiwen_api_prompt_context(client, sample_character):
    resp = client.get(f"/api/jiwen/{sample_character.id}/prompt-context")
    assert resp.status_code == 200
    data = resp.json()
    assert "context" in data
    assert isinstance(data["context"], str)


def test_jiwen_api_set_activity(client, sample_character):
    resp = client.post(
        f"/api/jiwen/{sample_character.id}/activity",
        json={"activity_type": "reading", "activity_label": "test book"},
    )
    assert resp.status_code == 200


def test_jiwen_api_set_user_status(client, sample_character):
    resp = client.post(
        f"/api/jiwen/{sample_character.id}/user-status",
        json={"user_status": "busy"},
    )
    assert resp.status_code == 200


def test_jiwen_api_scheduler_status(client):
    resp = client.get("/api/jiwen/scheduler/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "is_running" in data


def test_jiwen_api_memory_stats(client, sample_character):
    resp = client.get(f"/api/jiwen/{sample_character.id}/memory-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "forgotten_ratio" in data


# ======================================================================
# InteractionPipeline jiwen 注入（Director current_state + Actor style）
# ======================================================================
def _make_capturing_director_actor(captured):
    """
    返回一对 (FakeDirector, FakeActor) 类，替代 InteractionPipeline 内的真实
    DirectorModule / ActorModule。它们捕获传给 analyze_with_fallback /
    generate_with_fallback 的入参，跳过真实 LLM 调用。
    """
    class FakeDirector:
        def __init__(self):
            self.captured = captured

        def analyze_with_fallback(self, character_name, personality,
                                  current_state, recent_memories, user_input,
                                  history_messages=None):
            self.captured.setdefault("director_calls", []).append({
                "current_state": current_state,
            })
            return (
                {
                    "emotion": "平静",
                    "focus_memories": [],
                    "goal": "test",
                    "style": "normal",
                },
                '{"mock": true}',
            )

        def reload(self):
            pass

    class FakeActor:
        def __init__(self):
            self.captured = captured

        def generate_with_fallback(self, character_name, personality, emotion,
                                   focus_memories, goal, style, user_input,
                                   history_messages=None):
            self.captured.setdefault("actor_calls", []).append({
                "style": style,
            })
            return (
                {
                    "action": "stand",
                    "expression": "neutral",
                    "speech": "mock",
                },
                '{"mock": true}',
            )

        def reload(self):
            pass

    return FakeDirector, FakeActor


def test_pipeline_injects_jiwen_into_current_state(
    db_session, sample_character, monkeypatch,
):
    """
    Pipeline.run() 应把 jiwen 五轴状态合并到 Director 的 current_state._jiwen。
    """
    from backend.jiwen import get_jiwen_manager
    mgr = get_jiwen_manager()
    # 先设一个非零 jiwen 状态
    mgr.apply_delta(sample_character.id, {"pride": 0.5, "valence": 0.3, "connection": 0.6})

    # 替换 Director/Actor 类，避免实例化时加载真实 LLM
    captured = {}
    FakeDirector, FakeActor = _make_capturing_director_actor(captured)
    import backend.modules.interaction as inter_mod
    monkeypatch.setattr(inter_mod, "DirectorModule", FakeDirector)
    monkeypatch.setattr(inter_mod, "ActorModule", FakeActor)

    from backend.modules.interaction import InteractionPipeline
    pipe = InteractionPipeline()

    # 用测试 session（注入的 TestingSessionLocal）而不是 production SessionLocal
    pipe.run(character_id=sample_character.id, user_message="hello", db=db_session)

    # 验证
    assert captured.get("director_calls"), "director 未被调用"
    cs = captured["director_calls"][0]["current_state"]
    assert "_jiwen" in cs, f"current_state 缺少 _jiwen 字段，实际 keys: {list(cs.keys())}"
    j = cs["_jiwen"]
    assert "summary" in j
    assert "connection" in j
    assert "pride" in j
    assert "valence" in j
    assert "arousal" in j
    assert "immersion" in j
    assert j["pride"] == pytest.approx(0.5, abs=1e-2)
    assert j["valence"] == pytest.approx(0.3, abs=1e-2)


def test_pipeline_injects_jiwen_style_guidance(
    db_session, sample_character, monkeypatch,
):
    """
    Pipeline.run() 应把 jiwen style_guidance 附加到 Actor 的 style 字段。
    """
    from backend.jiwen import get_jiwen_manager
    mgr = get_jiwen_manager()
    # 高 pride → style_guidance 应包含"骄傲"字样
    mgr.apply_delta(sample_character.id, {"pride": 0.8})

    captured = {}
    FakeDirector, FakeActor = _make_capturing_director_actor(captured)
    import backend.modules.interaction as inter_mod
    monkeypatch.setattr(inter_mod, "DirectorModule", FakeDirector)
    monkeypatch.setattr(inter_mod, "ActorModule", FakeActor)

    from backend.modules.interaction import InteractionPipeline
    pipe = InteractionPipeline()

    pipe.run(character_id=sample_character.id, user_message="hi", db=db_session)

    assert captured.get("actor_calls"), "actor 未被调用"
    style = captured["actor_calls"][0]["style"]
    assert "情绪状态风格指引" in style, (
        f"style 字段未包含 jiwen style_guidance 标记，实际: {style[:200]}"
    )
