"""
test_performance_router — 缓存统计与手动失效端点契约测试。

覆盖：
  GET  /api/performance/cache-stats                 响应缓存 stats
  POST /api/performance/cache-invalidate            响应缓存清空（按 cid 过滤）
  GET  /api/performance/char-data-cache-stats       角色数据缓存 stats
  POST /api/performance/char-data-cache-invalidate  角色数据缓存清空

设计：
  - 端点本身是无状态包装，真实测试意义在于验证 stats 字段齐全 + invalidate 计数正确
  - 通过直接 import 模块的 _cache_put / _char_data_cache_put 注入测试数据
  - autouse fixture 每个测试前清 _response_cache / _char_data_cache / 计数
"""
from __future__ import annotations

import pytest
from backend.modules import interaction as ix


@pytest.fixture(autouse=True)
def _reset_caches():
    """每个测试前重置两个 cache 到空状态 + 计数清零。"""
    with ix._cache_lock:
        ix._response_cache.clear()
        ix._cache_hits = 0
        ix._cache_misses = 0
    with ix._char_data_lock:
        ix._char_data_cache.clear()
        ix._char_data_hits = 0
        ix._char_data_misses = 0
    yield
    with ix._cache_lock:
        ix._response_cache.clear()
        ix._cache_hits = 0
        ix._cache_misses = 0
    with ix._char_data_lock:
        ix._char_data_cache.clear()
        ix._char_data_hits = 0
        ix._char_data_misses = 0


# ============================================================
# /cache-stats（响应缓存）
# ============================================================
def test_response_cache_stats_empty(client):
    r = client.get("/api/performance/cache-stats")
    assert r.status_code == 200
    body = r.json()
    # 关键字段齐全
    for key in ("size", "max_size", "ttl_sec", "hits", "misses", "hit_rate"):
        assert key in body
    assert body["size"] == 0
    assert body["hits"] == 0
    assert body["misses"] == 0
    assert body["hit_rate"] == 0.0
    assert body["max_size"] > 0
    assert body["ttl_sec"] > 0


def test_response_cache_stats_reflects_puts_and_hits(client):
    # 注入 3 条
    ix._cache_put("1:hello", {"text": "hi"})
    ix._cache_put("1:world", {"text": "world"})
    ix._cache_put("2:foo", {"text": "foo"})
    # 触发 2 次 hit / 1 次 miss
    assert ix._cache_get("1:hello") == {"text": "hi"}
    assert ix._cache_get("2:foo") == {"text": "foo"}
    assert ix._cache_get("3:notexist") is None

    r = client.get("/api/performance/cache-stats")
    body = r.json()
    assert body["size"] == 3
    assert body["hits"] == 2
    assert body["misses"] == 1
    # hit_rate = 2/3 ≈ 0.6667
    assert abs(body["hit_rate"] - 2/3) < 0.01


def test_response_cache_invalidate_all(client):
    ix._cache_put("1:a", {"x": 1})
    ix._cache_put("2:b", {"x": 2})

    r = client.post("/api/performance/cache-invalidate")
    assert r.status_code == 200
    body = r.json()
    assert body["invalidated"] == 2
    assert body["character_id"] is None

    # 缓存真的清空了
    stats = client.get("/api/performance/cache-stats").json()
    assert stats["size"] == 0


def test_response_cache_invalidate_by_character(client):
    ix._cache_put("1:a", {"x": 1})
    ix._cache_put("1:b", {"x": 2})
    ix._cache_put("2:c", {"x": 3})
    ix._cache_put("3:d", {"x": 4})

    r = client.post("/api/performance/cache-invalidate?character_id=1")
    assert r.status_code == 200
    body = r.json()
    assert body["invalidated"] == 2
    assert body["character_id"] == 1

    # 1 的两条被清掉，2 和 3 还在
    stats = client.get("/api/performance/cache-stats").json()
    assert stats["size"] == 2


def test_response_cache_invalidate_no_match(client):
    ix._cache_put("5:a", {"x": 1})

    r = client.post("/api/performance/cache-invalidate?character_id=999")
    body = r.json()
    assert body["invalidated"] == 0
    # 原缓存未动
    assert client.get("/api/performance/cache-stats").json()["size"] == 1


# ============================================================
# /char-data-cache-stats（角色基础数据缓存）
# ============================================================
def test_char_data_cache_stats_empty(client):
    r = client.get("/api/performance/char-data-cache-stats")
    assert r.status_code == 200
    body = r.json()
    for key in ("size", "max_size", "ttl_sec", "hits", "misses", "hit_rate"):
        assert key in body
    assert body["size"] == 0
    assert body["max_size"] > 0
    assert body["ttl_sec"] > 0
    assert body["hit_rate"] == 0.0


def test_char_data_cache_stats_reflects_puts_and_hits(client):
    """stats 端点：_char_data_cache 注入 2 条 + 用 _bump_char_data 触发 1 hit / 1 miss。
    （_char_data_cache_get 本身不 bump 计数，只有上层 wrapper 在命中/未命中时调用 _bump_char_data。）"""
    ix._char_data_cache_put(1, {"empathy": 8}, {"mood": "happy"})
    ix._char_data_cache_put(2, {"logic": 9}, {"mood": "calm"})
    # 模拟上层 wrapper 行为：命中 +1 hit，未命中 +1 miss
    ix._bump_char_data(True)
    ix._bump_char_data(False)

    r = client.get("/api/performance/char-data-cache-stats")
    body = r.json()
    assert body["size"] == 2
    assert body["hits"] == 1
    assert body["misses"] == 1
    assert abs(body["hit_rate"] - 0.5) < 0.01


def test_char_data_cache_stats_no_bumps_stays_zero(client):
    """_char_data_cache_get 本身不增计数，stats 仍为 0。"""
    ix._char_data_cache_put(1, {"empathy": 8}, {"mood": "happy"})
    ix._char_data_cache_get(1)  # 命中但不计

    r = client.get("/api/performance/char-data-cache-stats")
    body = r.json()
    assert body["size"] == 1
    assert body["hits"] == 0
    assert body["misses"] == 0
    assert body["hit_rate"] == 0.0


def test_char_data_cache_invalidate_all(client):
    ix._char_data_cache_put(1, {}, {})
    ix._char_data_cache_put(2, {}, {})

    r = client.post("/api/performance/char-data-cache-invalidate")
    body = r.json()
    assert body["invalidated"] == 2
    assert body["character_id"] is None

    stats = client.get("/api/performance/char-data-cache-stats").json()
    assert stats["size"] == 0


def test_char_data_cache_invalidate_by_character(client):
    ix._char_data_cache_put(1, {"x": 1}, {})
    ix._char_data_cache_put(2, {"x": 2}, {})

    r = client.post("/api/performance/char-data-cache-invalidate?character_id=1")
    body = r.json()
    assert body["invalidated"] == 1
    assert body["character_id"] == 1

    # cid=2 还在
    assert client.get("/api/performance/char-data-cache-stats").json()["size"] == 1


def test_char_data_cache_invalidate_no_match(client):
    ix._char_data_cache_put(5, {"x": 1}, {})

    r = client.post("/api/performance/char-data-cache-invalidate?character_id=999")
    body = r.json()
    assert body["invalidated"] == 0
    assert client.get("/api/performance/char-data-cache-stats").json()["size"] == 1
