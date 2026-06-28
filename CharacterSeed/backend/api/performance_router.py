"""
performance_router — 缓存统计与手动失效（性能监控端点）。

端点：
  GET  /api/performance/cache-stats            响应缓存命中情况
  POST /api/performance/cache-invalidate       清空响应缓存（可选按 character_id）
  GET  /api/performance/char-data-cache-stats  角色数据解析缓存命中情况
  POST /api/performance/char-data-cache-invalidate  清空角色数据解析缓存

设计：
  - 用于性能监控与调优（前端 StatusPage 拉取 stats）
  - 手动失效是紧急清理工具，调试时偶尔使用
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter

from backend.modules.interaction import (
    cache_stats as response_cache_stats,
    cache_invalidate as invalidate_response_cache,
    char_data_cache_invalidate,
    char_data_cache_stats as char_data_cache_stats_fn,
)

router = APIRouter(prefix="/api/performance", tags=["performance"])


@router.get("/cache-stats")
def get_cache_stats():
    """
    返回响应缓存的命中情况。
    字段：size / max_size / ttl_sec / hits / misses / hit_rate
    """
    return response_cache_stats()


@router.post("/cache-invalidate")
def clear_cache(character_id: Optional[int] = None):
    """清空响应缓存。可选按 character_id 过滤。"""
    n = invalidate_response_cache(character_id=character_id)
    return {"invalidated": n, "character_id": character_id}


@router.get("/char-data-cache-stats")
def get_char_data_cache_stats():
    """返回角色基础数据解析缓存的命中情况。"""
    return char_data_cache_stats_fn()


@router.post("/char-data-cache-invalidate")
def clear_char_data_cache(character_id: Optional[int] = None):
    """清空角色基础数据解析缓存（手动调试用）。"""
    n = char_data_cache_invalidate(character_id)
    return {"invalidated": n, "character_id": character_id}
