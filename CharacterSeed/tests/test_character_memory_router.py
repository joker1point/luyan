"""
test_character_memory_router — 角色记忆/对话/成长读路径契约测试。

覆盖：
  GET /api/characters/{id}/memories       记忆列表（含 memory_type 过滤 + 分页）
  GET /api/characters/{id}/conversations  对话历史（按时间升序）
  GET /api/characters/{id}/growth-logs    成长记录（按时间降序）

设计：只读端点，绕开 LLM；用 crud 直插数据，验证序列化和过滤。
"""
from __future__ import annotations

from backend.crud import (
    memory as memory_crud,
    conversation as conv_crud,
    growth as growth_crud,
)


# ============================================================
# /memories
# ============================================================
def test_list_memories_empty(client, sample_character):
    r = client.get(f"/api/characters/{sample_character.id}/memories")
    assert r.status_code == 200
    assert r.json() == []


def test_list_memories_returns_all_types(client, sample_character, db):
    memory_crud.create_memory(db, sample_character.id, "evt-A", memory_type="event")
    memory_crud.create_memory(db, sample_character.id, "conv-A", memory_type="conversation")
    memory_crud.create_memory(db, sample_character.id, "growth-A", memory_type="growth")
    db.commit()

    r = client.get(f"/api/characters/{sample_character.id}/memories")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    types = {m["memory_type"] for m in data}
    assert types == {"event", "conversation", "growth"}


def test_list_memories_filter_by_type(client, sample_character, db):
    memory_crud.create_memory(db, sample_character.id, "evt-1", memory_type="event")
    memory_crud.create_memory(db, sample_character.id, "evt-2", memory_type="event")
    memory_crud.create_memory(db, sample_character.id, "conv-1", memory_type="conversation")
    db.commit()

    r = client.get(
        f"/api/characters/{sample_character.id}/memories?memory_type=event"
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert all(m["memory_type"] == "event" for m in data)


def test_list_memories_pagination(client, sample_character, db):
    for i in range(5):
        memory_crud.create_memory(db, sample_character.id, f"mem-{i}")
    db.commit()

    r = client.get(
        f"/api/characters/{sample_character.id}/memories?skip=2&limit=2"
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2


def test_list_memories_response_fields(client, sample_character, db):
    m = memory_crud.create_memory(
        db, sample_character.id, "重要的事", importance=8, memory_type="event",
    )
    db.commit()

    r = client.get(f"/api/characters/{sample_character.id}/memories")
    assert r.status_code == 200
    item = r.json()[0]
    # 校验所有 MemoryResponse 字段
    assert item["id"] == m.id
    assert item["character_id"] == sample_character.id
    assert item["content"] == "重要的事"
    assert item["importance"] == 8
    assert item["memory_type"] == "event"
    assert "created_at" in item


def test_list_memories_other_character_excluded(
    client, sample_character, sample_character_2, db,
):
    memory_crud.create_memory(db, sample_character.id, "我的")
    memory_crud.create_memory(db, sample_character_2.id, "别人的")
    db.commit()

    r = client.get(f"/api/characters/{sample_character.id}/memories")
    data = r.json()
    assert len(data) == 1
    assert data[0]["content"] == "我的"


# ============================================================
# /conversations
# ============================================================
def test_list_conversations_empty(client, sample_character):
    r = client.get(f"/api/characters/{sample_character.id}/conversations")
    assert r.status_code == 200
    assert r.json() == []


def test_list_conversations_ordered_ascending(client, sample_character, db):
    c1 = conv_crud.create_conversation(
        db, sample_character.id, "hi", "hello",
    )
    c2 = conv_crud.create_conversation(
        db, sample_character.id, "how are you", "fine",
    )
    c3 = conv_crud.create_conversation(
        db, sample_character.id, "bye", "see you",
    )
    db.commit()

    r = client.get(f"/api/characters/{sample_character.id}/conversations")
    assert r.status_code == 200
    data = r.json()
    assert [d["id"] for d in data] == [c1.id, c2.id, c3.id]
    # 关键字段都返回
    assert data[0]["user_input"] == "hi"
    assert data[0]["npc_response"] == "hello"
    assert data[0]["character_id"] == sample_character.id


def test_list_conversations_pagination(client, sample_character, db):
    for i in range(7):
        conv_crud.create_conversation(
            db, sample_character.id, f"u{i}", f"n{i}",
        )
    db.commit()

    r = client.get(
        f"/api/characters/{sample_character.id}/conversations?skip=5&limit=2"
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["user_input"] == "u5"


def test_list_conversations_other_character_excluded(
    client, sample_character, sample_character_2, db,
):
    conv_crud.create_conversation(db, sample_character.id, "mine", "ok")
    conv_crud.create_conversation(db, sample_character_2.id, "others", "ok")
    db.commit()

    r = client.get(f"/api/characters/{sample_character.id}/conversations")
    data = r.json()
    assert len(data) == 1
    assert data[0]["user_input"] == "mine"


# ============================================================
# /growth-logs
# ============================================================
def test_list_growth_logs_empty(client, sample_character):
    r = client.get(f"/api/characters/{sample_character.id}/growth-logs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_growth_logs_ordered_descending(client, sample_character, db):
    import time
    g1 = growth_crud.create_growth_log(
        db, sample_character.id,
        personality_delta='{"empathy": 1}',
        event_summary="第一次成长",
    )
    # SQLite 默认 created_at 精度 = 秒，连续 3 条同秒会拿到相同 ts，
    # 导致 desc 排序在不同会话间结果不稳定。
    # 隔 1.1 秒确保时间戳严格递增。
    time.sleep(1.1)
    g2 = growth_crud.create_growth_log(
        db, sample_character.id,
        personality_delta='{"logic": 2}',
        event_summary="第二次成长",
    )
    time.sleep(1.1)
    g3 = growth_crud.create_growth_log(
        db, sample_character.id,
        personality_delta='{"optimism": 1}',
        event_summary="第三次成长",
    )
    db.commit()

    r = client.get(f"/api/characters/{sample_character.id}/growth-logs")
    assert r.status_code == 200
    data = r.json()
    # 降序：最新在前（按 created_at desc）
    assert [d["id"] for d in data] == [g3.id, g2.id, g1.id]
    # 校验字段透传
    assert data[0]["personality_delta"] == '{"optimism": 1}'
    assert data[0]["event_summary"] == "第三次成长"
    assert data[0]["character_id"] == sample_character.id


def test_list_growth_logs_pagination(client, sample_character, db):
    for i in range(6):
        growth_crud.create_growth_log(
            db, sample_character.id, event_summary=f"growth-{i}",
        )
    db.commit()

    r = client.get(
        f"/api/characters/{sample_character.id}/growth-logs?skip=2&limit=3"
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3


def test_list_growth_logs_other_character_excluded(
    client, sample_character, sample_character_2, db,
):
    growth_crud.create_growth_log(db, sample_character.id, event_summary="我的成长")
    growth_crud.create_growth_log(db, sample_character_2.id, event_summary="别人成长")
    db.commit()

    r = client.get(f"/api/characters/{sample_character.id}/growth-logs")
    data = r.json()
    assert len(data) == 1
    assert data[0]["event_summary"] == "我的成长"
