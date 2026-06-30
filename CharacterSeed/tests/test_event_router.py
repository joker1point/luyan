"""
test_event_router — 事件推进 / 时间迭代端点契约测试。

覆盖：
  GET  /api/characters/{id}/events   列表（按 day/status 过滤）
  POST /api/event/advance            推进单个 pending（LLM 替代为 fake event_manager）
  POST /api/time/iterate             日迭代（fake time_engine 直接返回结构化 dict）
  POST /api/time/auto                一键推演（fake 返回结构化 dict）

设计：
  - 真实 EventManager / TimeEngine 会调 LLM，测试用 backend.state._singletons
    注入 fake 替代，验证路由 + 入参/出参契约。
  - 角色不存在 → 路由层 404 短路，不依赖 fake。
"""
from __future__ import annotations

import json
from datetime import datetime

from backend import state as backend_state
from backend.crud import event as event_crud


# ============================================================
# Fake 单例
# ============================================================
class FakeEventManager:
    """替代 EventManager.advance_one(db, character_id) 的伪实现。"""

    def __init__(self, return_value=None, raise_value_error=False, raise_other=None):
        self.return_value = return_value
        self.raise_value_error = raise_value_error
        self.raise_other = raise_other
        self.reload_called = False

    def advance_one(self, db, character_id):
        self.reload_called = True
        if self.raise_value_error:
            raise ValueError("fake: no pending event")
        if self.raise_other is not None:
            raise self.raise_other
        return self.return_value

    def reload(self):
        pass


class FakeTimeEngine:
    """替代 TimeEngine.iterate / auto 的伪实现。"""

    def __init__(self, iterate_result=None, auto_result=None):
        self.iterate_result = iterate_result or {
            "growth_log_id": 1,
            "character_id": 1,
            "day_number": 2,
            "personality_delta": '{"empathy": 1}',
            "event_summary": "今天有进展",
            "new_memories": "[]",
            "world_changes_json": '{"world_changes": "世界略有变化"}',
            "schedule_json": '{"schedule": []}',
            "events_created": 3,
            "growth_raw": "{}",
            "created_at": datetime.utcnow(),
        }
        self.auto_result = auto_result or {
            "character_id": 1,
            "completed_events": [],
            "iterate_result": self.iterate_result,
            "error": None,
        }
        self.reload_called = False

    def iterate(self, db, character_id):
        self.reload_called = True
        return self.iterate_result

    def auto(self, db, character_id):
        self.reload_called = True
        return self.auto_result

    def reload(self):
        pass


# ============================================================
# /events 列表
# ============================================================
def test_list_events_empty(client, sample_character):
    r = client.get(f"/api/characters/{sample_character.id}/events")
    assert r.status_code == 200
    assert r.json() == []


def test_list_events_returns_all_ordered(client, sample_character, db):
    e1 = event_crud.create_event(
        db, sample_character.id, day_number=1, order_index=0, event_type="schedule_action", content="晨读",
    )
    e2 = event_crud.create_event(
        db, sample_character.id, day_number=1, order_index=1, event_type="schedule_action", content="午餐",
    )
    e3 = event_crud.create_event(
        db, sample_character.id, day_number=2, order_index=0, event_type="schedule_action", content="远足",
    )
    db.commit()

    r = client.get(f"/api/characters/{sample_character.id}/events")
    assert r.status_code == 200
    data = r.json()
    # 按 day, order 升序
    assert [d["id"] for d in data] == [e1.id, e2.id, e3.id]


def test_list_events_filter_by_day(client, sample_character, db):
    event_crud.create_event(db, sample_character.id, day_number=1, order_index=0, event_type="schedule_action", content="d1")
    event_crud.create_event(db, sample_character.id, day_number=2, order_index=0, event_type="schedule_action", content="d2")
    event_crud.create_event(db, sample_character.id, day_number=2, order_index=1, event_type="schedule_action", content="d2-2")
    db.commit()

    r = client.get(
        f"/api/characters/{sample_character.id}/events?day_number=2"
    )
    data = r.json()
    assert len(data) == 2
    assert all(d["day_number"] == 2 for d in data)


def test_list_events_filter_by_status(client, sample_character, db):
    event_crud.create_event(
        db, sample_character.id, day_number=1, order_index=0, event_type="schedule_action", content="p1", status="pending",
    )
    event_crud.create_event(
        db, sample_character.id, day_number=1, order_index=1, event_type="schedule_action", content="c1", status="completed",
    )
    db.commit()

    r = client.get(
        f"/api/characters/{sample_character.id}/events?status=pending"
    )
    data = r.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending"


def test_list_events_character_not_found(client):
    r = client.get("/api/characters/9999/events")
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]


# ============================================================
# /api/event/advance
# ============================================================
def test_advance_event_success(client, sample_character, db, monkeypatch):
    """fake EventManager 返回一个 Event 行，路由直接透传。"""
    pending = event_crud.create_event(
        db, sample_character.id, day_number=1, order_index=0,
        event_type="schedule_action", content="pending event", status="pending",
    )
    db.commit()

    # fake advance_one：直接把 pending 标 completed
    def fake_advance(db, cid):
        return event_crud.update_event_result(
            db, pending.id, result_json='{"ok":true}', status="completed",
        )

    fake = FakeEventManager(return_value=None)
    fake.advance_one = fake_advance
    monkeypatch.setitem(backend_state._singletons, "event_manager", fake)

    r = client.post(
        "/api/event/advance",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["result_json"] == '{"ok":true}'


def test_advance_event_no_pending_returns_404(client, sample_character, monkeypatch):
    """fake 返回 None（无 pending）→ 路由 404 + 友好文案。"""
    fake = FakeEventManager(return_value=None)
    monkeypatch.setitem(backend_state._singletons, "event_manager", fake)

    r = client.post(
        "/api/event/advance",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "pending" in detail
    assert str(sample_character.id) in detail


def test_advance_event_value_error_returns_404(client, sample_character, monkeypatch):
    """fake 抛 ValueError → 路由 404（透传 e）。"""
    fake = FakeEventManager(raise_value_error=True)
    monkeypatch.setitem(backend_state._singletons, "event_manager", fake)

    r = client.post(
        "/api/event/advance",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 404
    assert "fake" in r.json()["detail"]


def test_advance_event_runtime_error_returns_500(client, sample_character, monkeypatch):
    """fake 抛其他 Exception → 路由 500。"""
    fake = FakeEventManager(raise_other=RuntimeError("LLM 炸了"))
    monkeypatch.setitem(backend_state._singletons, "event_manager", fake)

    r = client.post(
        "/api/event/advance",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 500
    assert "LLM 炸了" in r.json()["detail"]


def test_advance_event_character_not_found(client, monkeypatch):
    """角色不存在 → 路由 404 短路（不调 fake）。"""
    fake = FakeEventManager()
    monkeypatch.setitem(backend_state._singletons, "event_manager", fake)
    r = client.post(
        "/api/event/advance",
        json={"character_id": 9999},
    )
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]


# ============================================================
# /api/time/iterate
# ============================================================
def test_iterate_success(client, sample_character, monkeypatch):
    fake = FakeTimeEngine()
    monkeypatch.setitem(backend_state._singletons, "time_engine", fake)

    r = client.post(
        "/api/time/iterate",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["character_id"] == sample_character.id
    assert body["day_number"] == 2
    assert body["growth_log_id"] == 1
    assert body["events_created"] == 3
    # JSON 字符串字段
    parsed = json.loads(body["world_changes_json"])
    assert "world_changes" in parsed


def test_iterate_value_error_returns_404(client, sample_character, monkeypatch):
    class BoomIter(FakeTimeEngine):
        def iterate(self, db, cid):
            raise ValueError("角色未初始化")

    monkeypatch.setitem(backend_state._singletons, "time_engine", BoomIter())
    r = client.post(
        "/api/time/iterate",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 404
    assert "角色未初始化" in r.json()["detail"]


def test_iterate_runtime_error_returns_500(client, sample_character, monkeypatch):
    class BoomIter(FakeTimeEngine):
        def iterate(self, db, cid):
            raise RuntimeError("LLM 不可用")

    monkeypatch.setitem(backend_state._singletons, "time_engine", BoomIter())
    r = client.post(
        "/api/time/iterate",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 500
    assert "LLM 不可用" in r.json()["detail"]


def test_iterate_character_not_found(client, monkeypatch):
    """路由 404 短路：先检查角色再调 fake。"""
    fake = FakeTimeEngine()
    monkeypatch.setitem(backend_state._singletons, "time_engine", fake)
    r = client.post(
        "/api/time/iterate",
        json={"character_id": 9999},
    )
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]
    # fake.iterate 不该被调用
    assert not fake.reload_called  # 走的是 reload；只看 iterate 调用次数
    # iterate() 没被调用


# ============================================================
# /api/time/auto
# ============================================================
def test_auto_success(client, sample_character, monkeypatch):
    fake = FakeTimeEngine()
    monkeypatch.setitem(backend_state._singletons, "time_engine", fake)

    r = client.post(
        "/api/time/auto",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["character_id"] == sample_character.id
    assert body["error"] is None
    # iterate_result 字段透传
    assert body["iterate_result"]["day_number"] == 2
    # completed_events 默认空 list
    assert body["completed_events"] == []


def test_auto_with_completed_events(client, sample_character, db, monkeypatch):
    """auto 返回带 completed_events + iterate_result 的结构化响应。"""
    e = event_crud.create_event(
        db, sample_character.id, day_number=1, order_index=0,
        event_type="schedule_action", content="event", status="completed",
    )
    e.result_json = "{}"
    db.commit()

    auto_dict = {
        "character_id": sample_character.id,
        "completed_events": [{
            "id": e.id, "character_id": sample_character.id, "day_number": 1,
            "order_index": 0, "event_type": "schedule_action", "content": "event",
            "metadata_json": None, "result_json": "{}", "status": "completed",
            "session_id": None, "time_period": "morning",
            "created_at": e.created_at.isoformat() if e.created_at else "",
        }],
        "iterate_result": {
            "growth_log_id": 2, "character_id": sample_character.id, "day_number": 2,
            "personality_delta": None, "event_summary": None, "new_memories": None,
            "world_changes_json": None, "schedule_json": None, "events_created": 0,
            "growth_raw": None, "created_at": None,
        },
        "error": None,
    }
    fake = FakeTimeEngine(auto_result=auto_dict)
    monkeypatch.setitem(backend_state._singletons, "time_engine", fake)

    r = client.post(
        "/api/time/auto",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["completed_events"]) == 1
    assert body["completed_events"][0]["id"] == e.id
    assert body["iterate_result"]["growth_log_id"] == 2


def test_auto_error_in_response(client, sample_character, monkeypatch):
    """auto 内部错误（已捕获）→ 200 + error 字段，不抛 500。"""
    auto_dict = {
        "character_id": sample_character.id,
        "completed_events": [],
        "iterate_result": None,
        "error": "推进阶段异常: LLM 失败",
    }
    fake = FakeTimeEngine(auto_result=auto_dict)
    monkeypatch.setitem(backend_state._singletons, "time_engine", fake)

    r = client.post(
        "/api/time/auto",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 200
    assert r.json()["error"] == "推进阶段异常: LLM 失败"


def test_auto_runtime_error_returns_500(client, sample_character, monkeypatch):
    class BoomAuto(FakeTimeEngine):
        def auto(self, db, cid):
            raise RuntimeError("迭代阶段异常")

    monkeypatch.setitem(backend_state._singletons, "time_engine", BoomAuto())
    r = client.post(
        "/api/time/auto",
        json={"character_id": sample_character.id},
    )
    assert r.status_code == 500
    assert "迭代阶段异常" in r.json()["detail"]


def test_auto_character_not_found(client, monkeypatch):
    fake = FakeTimeEngine()
    monkeypatch.setitem(backend_state._singletons, "time_engine", fake)
    r = client.post(
        "/api/time/auto",
        json={"character_id": 9999},
    )
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]
