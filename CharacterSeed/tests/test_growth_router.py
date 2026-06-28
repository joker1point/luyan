"""
test_growth_router — 角色成长触发端点契约测试。

覆盖：
  POST /api/growth/trigger  触发成长：成功 / ValueError→404 / 其他异常→500

设计：
  - 真实 GrowthModule.run() 会调 LLM；测试用 backend.state._singletons 注入 fake。
  - 验证路由契约：success 时返回 GrowthResponse，error 时 detail 透传。
  - 成长后会自动调 cache_invalidate 清响应缓存（验证副作用）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import pytest

from backend import state as backend_state
from backend.modules import interaction as ix


# ============================================================
# Fake GrowthModule
# ============================================================
class FakeGrowthModule:
    """替代 GrowthModule.run() 的伪实现。"""

    def __init__(
        self,
        result: Dict[str, Any] | None = None,
        raise_value_error: str | None = None,
        raise_other: Exception | None = None,
    ):
        self.result = result or {
            "id": 42,
            "character_id": 1,
            "personality_delta": '{"empathy": 2}',
            "event_summary": "今天学到了耐心",
            "new_memories": '["记一次深刻的对话"]',
            "growth_raw": '{"raw": "raw LLM output"}',
            "created_at": datetime(2026, 6, 26, 10, 0, 0),
        }
        self.raise_value_error = raise_value_error
        self.raise_other = raise_other
        self.run_calls = []

    def run(self, character_id: int, db, conversation_limit: int = 10):
        self.run_calls.append((character_id, conversation_limit))
        if self.raise_value_error is not None:
            raise ValueError(self.raise_value_error)
        if self.raise_other is not None:
            raise self.raise_other
        # 透传 character_id，保证响应里的 id 一致
        result = dict(self.result)
        result["character_id"] = character_id
        return result

    def reload(self):
        pass


@pytest.fixture(autouse=True)
def _reset_response_cache():
    """每个测试前后清响应缓存，确保 invalidate 计数可观察。"""
    with ix._cache_lock:
        ix._response_cache.clear()
    yield
    with ix._cache_lock:
        ix._response_cache.clear()


def _seed_cache(char_id: int, n: int = 1) -> int:
    """往响应缓存塞 n 条指定 cid 的条目，返回实际塞入数。"""
    for i in range(n):
        ix._cache_put(f"{char_id}:seed-{i}", {"text": f"v{i}"})
    return n


# ============================================================
# /api/growth/trigger
# ============================================================
def test_trigger_success(client, sample_character, monkeypatch):
    """成功路径：fake GrowthModule 返回结构化 dict，路由原样返回。"""
    fake = FakeGrowthModule()
    monkeypatch.setitem(backend_state._singletons, "growth", fake)

    r = client.post(
        "/api/growth/trigger",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == 42
    assert body["character_id"] == sample_character.id
    assert body["personality_delta"] == '{"empathy": 2}'
    assert body["event_summary"] == "今天学到了耐心"
    assert body["new_memories"] == '["记一次深刻的对话"]'
    # run 被调用 1 次，传入的 conversation_limit 走默认 10
    assert len(fake.run_calls) == 1
    assert fake.run_calls[0][0] == sample_character.id


def test_trigger_invalidates_response_cache(client, sample_character, monkeypatch):
    """成长后应自动清掉该角色的响应缓存（人格/记忆变化后旧缓存失效）。"""
    fake = FakeGrowthModule()
    monkeypatch.setitem(backend_state._singletons, "growth", fake)

    # 预置 3 条该角色缓存 + 2 条其他角色缓存
    _seed_cache(sample_character.id, n=3)
    _seed_cache(999, n=2)
    assert ix.cache_stats()["size"] == 5

    r = client.post(
        "/api/growth/trigger",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 200

    # 该角色的 3 条被清，999 的 2 条还在
    stats = ix.cache_stats()
    assert stats["size"] == 2


def test_trigger_value_error_returns_404(client, sample_character, monkeypatch):
    """fake 抛 ValueError（角色不存在 / 业务校验失败）→ 404。"""
    fake = FakeGrowthModule(raise_value_error="角色不存在: id=9999")
    monkeypatch.setitem(backend_state._singletons, "growth", fake)

    r = client.post(
        "/api/growth/trigger",
        json={"character_id": 9999},
    )
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]


def test_trigger_runtime_error_returns_500(client, sample_character, monkeypatch):
    """fake 抛其他 Exception（LLM 失败）→ 500。"""
    fake = FakeGrowthModule(raise_other=RuntimeError("LLM 超时"))
    monkeypatch.setitem(backend_state._singletons, "growth", fake)

    r = client.post(
        "/api/growth/trigger",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 500
    assert "LLM 超时" in r.json()["detail"]


def test_trigger_empty_personality_delta(client, sample_character, monkeypatch):
    """personality_delta 为 None 时响应字段为 null（schema 允许 Optional）。"""
    result = {
        "id": 1,
        "character_id": sample_character.id,
        "personality_delta": None,
        "event_summary": None,
        "new_memories": None,
        "growth_raw": None,
        "created_at": datetime(2026, 6, 26, 0, 0, 0),
    }
    fake = FakeGrowthModule(result=result)
    monkeypatch.setitem(backend_state._singletons, "growth", fake)

    r = client.post(
        "/api/growth/trigger",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["personality_delta"] is None
    assert body["event_summary"] is None


def test_trigger_missing_character_id(client):
    """Pydantic 校验失败 → 422（character_id 必填）。"""
    r = client.post("/api/growth/trigger", json={})
    assert r.status_code == 422
    assert "detail" in r.json()
