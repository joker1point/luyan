"""
test_character_router — 角色 CRUD + 描述润色端点的契约测试。

覆盖：
  POST   /api/characters/create                角色创建（text / 400 / 500）
  GET    /api/characters                       角色列表 + 分页
  GET    /api/characters/{id}                  单角色详情 + 404
  DELETE /api/characters/{id}                  级联删除（验证关联表也被清掉）
  POST   /api/characters/polish-description    一句话润色（mock LLM）
"""
from __future__ import annotations

import json


# ============================================================
# 列表
# ============================================================
def test_list_characters_empty(client):
    r = client.get("/api/characters")
    assert r.status_code == 200
    assert r.json() == []


def test_list_characters_returns_all(client, sample_character, sample_character_2):
    r = client.get("/api/characters")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    names = {c["name"] for c in data}
    assert names == {"苏晴", "李墨"}


def test_list_characters_pagination(client, sample_character, sample_character_2):
    r = client.get("/api/characters?skip=1&limit=1")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1


# ============================================================
# 详情
# ============================================================
def test_get_character_success(client, sample_character):
    r = client.get(f"/api/characters/{sample_character.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == sample_character.id
    assert body["name"] == "苏晴"
    # personality 是 JSON 字符串
    assert json.loads(body["personality"]) == {"empathy": 8, "optimism": 7}


def test_get_character_not_found(client):
    r = client.get("/api/characters/9999")
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]


# ============================================================
# 创建（text 模式）
# ============================================================
def test_create_character_text_success(client, mock_creation_module):
    r = client.post(
        "/api/characters/create",
        data={"description": "一个沉默的程序员"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Mocked 角色"
    assert body["description"] == "一个沉默的程序员"
    # personality dict → JSON 字符串
    parsed_personality = json.loads(body["personality"])
    assert parsed_personality == {"empathy": 5, "optimism": 5}


def test_create_character_text_writes_initial_memories(
    client, mock_creation_module, db,
):
    from backend.models import Memory
    r = client.post(
        "/api/characters/create",
        data={"description": "测试初始记忆"},
    )
    assert r.status_code == 200
    char_id = r.json()["id"]
    mems = db.query(Memory).filter(Memory.character_id == char_id).all()
    assert len(mems) == 1
    assert mems[0].content == "mock 初始记忆"
    assert mems[0].memory_type == "event"


def test_create_character_missing_both_inputs(client):
    r = client.post("/api/characters/create", data={})
    assert r.status_code == 400
    assert "必须提供" in r.json()["detail"]


def test_create_character_file_mode(client, mock_creation_module):
    """file 模式：上传 txt 文件 → description 作为额外期望追加。"""
    from io import BytesIO
    content = "苏晴是一名江城的高中语文老师。".encode("utf-8")
    r = client.post(
        "/api/characters/create",
        files={"story_file": ("story.txt", BytesIO(content), "text/plain")},
        data={"description": "她很温柔"},
    )
    assert r.status_code == 200, r.text
    desc = r.json()["description"]
    assert "苏晴" in desc
    assert "她很温柔" in desc
    # 长度被截断到 500
    assert len(desc) <= 500


def test_create_character_llm_error_returns_500(client, monkeypatch):
    """get_creation_module().run() 抛异常时 → 500 + 友好 detail。"""
    from backend import state as backend_state

    class BoomCreation:
        llm_service = type("L", (), {"call": lambda *a, **k: "x"})()

        def run(self, *args, **kwargs):
            raise RuntimeError("LLM 炸了")

        def reload(self):
            pass

    monkeypatch.setitem(backend_state._singletons, "creation", BoomCreation())
    r = client.post(
        "/api/characters/create",
        data={"description": "boom"},
    )
    assert r.status_code == 500
    assert "LLM 炸了" in r.json()["detail"]


# ============================================================
# 删除（级联）
# ============================================================
def test_delete_character_cascades_relations(client, sample_character, db):
    from backend.crud import (
        character as character_crud,
        conversation as conv_crud,
    )
    from backend.models import Memory

    # 预置关联数据
    conv_crud.create_conversation(
        db=db, character_id=sample_character.id,
        user_input="hi", npc_response="hello",
    )
    db.add(Memory(character_id=sample_character.id, content="test", memory_type="event"))
    db.commit()

    r = client.delete(f"/api/characters/{sample_character.id}")
    assert r.status_code == 200, r.text
    detail = r.json()["detail"]
    # 验证 detail 包含子表清理统计
    assert "苏晴" in detail
    assert "记忆" in detail
    assert "对话" in detail

    # 角色真的没了
    assert character_crud.get_character(db, sample_character.id) is None
    # 关联也被清
    assert db.query(Memory).filter(Memory.character_id == sample_character.id).count() == 0


def test_delete_character_not_found(client):
    r = client.delete("/api/characters/9999")
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]


# ============================================================
# 润色
# ============================================================
def test_polish_description_success(client, mock_creation_module):
    r = client.post(
        "/api/characters/polish-description",
        json={"description": "一个老师"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["polished"] == "润色后的描述文本"
    assert body["original"] == "一个老师"


def test_polish_description_empty_rejected(client, mock_creation_module):
    """空描述被 Pydantic min_length=1 拦截为 422（比路由内 manual check 更早）。"""
    r = client.post(
        "/api/characters/polish-description",
        json={"description": ""},
    )
    assert r.status_code == 422
    # Pydantic 错误体含 "at least 1 character" 之类的提示
    assert "detail" in r.json()


def test_polish_description_unwraps_quotes(client, mock_creation_module):
    """润色结果带引号时应被自动剥掉。"""
    from backend import state as backend_state

    class QuoteCreation:
        llm_service = type("L", (), {"call": lambda *a, **k: '"润色后带引号"'})()

        def run(self, *args, **kwargs):
            return {"name": "x"}, "{}"

        def reload(self):
            pass

    backend_state._singletons["creation"] = QuoteCreation()
    r = client.post(
        "/api/characters/polish-description",
        json={"description": "x"},
    )
    assert r.status_code == 200
    assert r.json()["polished"] == "润色后带引号"
