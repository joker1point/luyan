"""
test_memory_router — 增强记忆系统 /api/memory/* 契约测试。

重要：当前 main.py **没有** include_router(memory_router)，
  路由在生产环境是休眠的。本测试通过独立 FastAPI app 挂载该 router
  来验证它的契约正确性（这样未来 main.py 启用它时即可直接通过）。

覆盖：
  GET  /api/memory/health                 记忆系统健康（短/长/知 三模块）
  GET  /api/memory/stats/{cid}            记忆统计（含 user_id 维度）
  POST /api/memory/cache/clear            清空上下文缓存（all / by cid）
  POST /api/memory/add                    角色不存在 → 404 + 加成功
  POST /api/memory/search                 角色不存在 + limit 校验
  POST /api/memory/knowledge/add          角色不存在
  POST /api/memory/knowledge/search       角色不存在 + 搜索
  POST /api/memory/knowledge/upload       角色不存在（multipart）
  POST /api/memory/context/build          角色不存在 + 多模板 + include 过滤

设计：
  - 用独立 FastAPI() + include_router(memory_router.router) 隔离主 app 的依赖
  - 真实管线在没有 mem0/cognee 时退化为 JSON/文件本地存储，模块可正常初始化
"""
from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import memory_router
from backend.database import Base, get_db
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ============================================================
# 独立内存 SQLite + 独立 app（不污染主 conftest）
# ============================================================
_ISOLATED_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_ISOLATED_ENGINE, "connect")
def _enable_fks(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


IsolatedSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_ISOLATED_ENGINE)


def _isolated_get_db():
    db = IsolatedSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _reset_isolated_db():
    """每个测试前重置独立 DB。"""
    Base.metadata.drop_all(bind=_ISOLATED_ENGINE)
    Base.metadata.create_all(bind=_ISOLATED_ENGINE)
    yield


@pytest.fixture
def isolated_app():
    """独立 FastAPI app，挂在 memory_router 上。"""
    app = FastAPI()
    app.include_router(memory_router.router)
    app.dependency_overrides[get_db] = _isolated_get_db
    return app


@pytest.fixture
def client(isolated_app):
    return TestClient(isolated_app)


@pytest.fixture
def db():
    s = IsolatedSessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def sample_character(db):
    from backend.crud import character as character_crud
    return character_crud.create_character(
        db=db,
        name="苏晴",
        description="温柔的高中语文老师",
        world_setting="2026 年春，江城",
        personality={"empathy": 8, "optimism": 7},
        current_state={"mood": "happy"},
    )


# ============================================================
# /health
# ============================================================
def test_health_returns_modules(client):
    r = client.get("/api/memory/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert "modules" in body
    for name in ("short_term", "long_term", "knowledge_base"):
        mod = body["modules"][name]
        assert "available" in mod
        assert "engine" in mod
        assert isinstance(mod["engine"], str) and mod["engine"] != ""


# ============================================================
# /stats/{cid}
# ============================================================
def test_stats_character_not_found(client):
    r = client.get("/api/memory/stats/9999")
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]


def test_stats_success(client, sample_character):
    r = client.get(f"/api/memory/stats/{sample_character.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["character_id"] == sample_character.id
    assert body["character_name"] == "苏晴"
    assert "memory_stats" in body
    stats = body["memory_stats"]
    assert isinstance(stats, dict)
    for key in ("short_term_count", "long_term_count", "max_tokens", "long_term_limit", "knowledge_limit"):
        assert key in stats


def test_stats_with_user_id(client, sample_character):
    r = client.get(
        f"/api/memory/stats/{sample_character.id}?user_id=alice"
    )
    assert r.status_code == 200
    assert r.json()["character_id"] == sample_character.id


# ============================================================
# /cache/clear
# ============================================================
def test_cache_clear_all(client):
    r = client.post("/api/memory/cache/clear")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["cleared"] == "all"


def test_cache_clear_by_character(client):
    r = client.post("/api/memory/cache/clear?character_id=42")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["cleared"] == 42


# ============================================================
# 角色不存在短路
# ============================================================
def test_add_memory_character_not_found(client):
    r = client.post(
        "/api/memory/add",
        json={"character_id": 9999, "content": "x"},
    )
    assert r.status_code == 404
    assert "角色不存在" in r.json()["detail"]


def test_search_memories_character_not_found(client):
    r = client.post(
        "/api/memory/search",
        json={"character_id": 9999, "query": "x"},
    )
    assert r.status_code == 404


def test_knowledge_add_character_not_found(client):
    r = client.post(
        "/api/memory/knowledge/add",
        json={"character_id": 9999, "text": "x"},
    )
    assert r.status_code == 404


def test_knowledge_search_character_not_found(client):
    r = client.post(
        "/api/memory/knowledge/search",
        json={"character_id": 9999, "query": "x"},
    )
    assert r.status_code == 404


def test_context_build_character_not_found(client):
    r = client.post(
        "/api/memory/context/build",
        json={"character_id": 9999, "query": "x"},
    )
    assert r.status_code == 404


def test_knowledge_upload_character_not_found(client):
    r = client.post(
        "/api/memory/knowledge/upload?character_id=9999",
        files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 404


# ============================================================
# 端到端：add → search
# ============================================================
def test_add_and_search_long_term(client, sample_character):
    r1 = client.post(
        "/api/memory/add",
        json={
            "character_id": sample_character.id,
            "content": "用户喜欢喝龙井茶",
            "user_id": "alice",
        },
    )
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["success"] is True
    assert "memory_id" in body
    assert body["content"] == "用户喜欢喝龙井茶"
    assert body["character_id"] == sample_character.id

    r2 = client.post(
        "/api/memory/search",
        json={
            "character_id": sample_character.id,
            "query": "龙井",
            "user_id": "alice",
            "limit": 5,
        },
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["character_id"] == sample_character.id
    assert body["query"] == "龙井"
    assert "memories" in body
    assert isinstance(body["count"], int)
    assert body["count"] == len(body["memories"])


def test_search_limit_validation(client, sample_character):
    r = client.post(
        "/api/memory/search",
        json={"character_id": sample_character.id, "query": "x", "limit": 0},
    )
    assert r.status_code == 422
    r2 = client.post(
        "/api/memory/search",
        json={"character_id": sample_character.id, "query": "x", "limit": 100},
    )
    assert r2.status_code == 422


# ============================================================
# 端到端：knowledge
# ============================================================
def test_knowledge_add_success(client, sample_character):
    r = client.post(
        "/api/memory/knowledge/add",
        json={
            "character_id": sample_character.id,
            "text": "苏晴是一名江城高中语文老师，喜欢读《边城》。",
            "source": "test",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["character_id"] == sample_character.id
    assert body["length"] == len("苏晴是一名江城高中语文老师，喜欢读《边城》。")


def test_knowledge_search_returns_structure(client, sample_character):
    r = client.post(
        "/api/memory/knowledge/search",
        json={"character_id": sample_character.id, "query": "老师", "limit": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "老师"
    assert body["character_id"] == sample_character.id
    assert "results" in body
    assert isinstance(body["count"], int)
    assert body["count"] == len(body["results"])


# ============================================================
# 端到端：context/build
# ============================================================
def test_context_build_default_template(client, sample_character):
    r = client.post(
        "/api/memory/context/build",
        json={
            "character_id": sample_character.id,
            "query": "今天天气怎么样",
            "template": "default",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["character_id"] == sample_character.id
    assert body["query"] == "今天天气怎么样"
    assert body["template"] == "default"
    assert "context" in body
    assert "formatted_prompt" in body
    assert isinstance(body["context"], dict)


def test_context_build_excludes_layers(client, sample_character):
    r = client.post(
        "/api/memory/context/build",
        json={
            "character_id": sample_character.id,
            "query": "x",
            "include_short_term": False,
            "include_long_term": True,
            "include_knowledge": False,
        },
    )
    assert r.status_code == 200
    sources = r.json()["context"].get("metadata", {}).get("sources", [])
    assert "short_term" not in sources
    assert "knowledge_base" not in sources
