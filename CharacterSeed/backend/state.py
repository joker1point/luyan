"""
全局单例状态（从 main.py 抽出）。

设计动机：
  - main.py 原本用模块级单例（_creation_module / _pipeline / _growth_module /
    _event_manager / _time_engine）+ reload_all_llm()，所有这些都耦合在 main.py
    导致 main.py 膨胀到 1200+ 行。
  - 抽出后各 router 文件可以直接 from backend.state import get_pipeline()，
    既能复用单例，又不依赖 main.py，避免循环引用。

约定：
  - 每个 getter（get_xxx）懒加载：首次调用时实例化，后续复用同一对象。
  - reload_all_llm() 遍历所有可重载单例，统一调用 .reload()，
    并失效所有依赖旧 LLM 配置的缓存。
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 单例容器（key → instance）
_singletons: dict[str, Any] = {}


def get_creation_module():
    if "creation" not in _singletons:
        from backend.modules.creation import CreationModule
        _singletons["creation"] = CreationModule()
    return _singletons["creation"]


def get_pipeline():
    if "pipeline" not in _singletons:
        from backend.modules.interaction import InteractionPipeline
        _singletons["pipeline"] = InteractionPipeline()
    return _singletons["pipeline"]


def get_growth_module():
    if "growth" not in _singletons:
        from backend.modules.growth import GrowthModule
        _singletons["growth"] = GrowthModule()
    return _singletons["growth"]


def get_event_manager():
    if "event_manager" not in _singletons:
        from backend.modules.event import EventManager
        _singletons["event_manager"] = EventManager()
    return _singletons["event_manager"]


def get_time_engine():
    if "time_engine" not in _singletons:
        from backend.modules.time import TimeEngine
        _singletons["time_engine"] = TimeEngine()
    return _singletons["time_engine"]


def reload_all_llm() -> None:
    """
    设置页改动后调用，热更新所有单例的 LLM 配置（复用已加载的 prompt 模板）。

    行为：
      1) 遍历所有持 .reload() 方法的单例（自然排除 context_manager 等持久缓存）
      2) 切换 provider → 旧 provider 的响应缓存 + 角色数据缓存全部失效
      3) 写一行 INFO 便于审计

    注意：新增业务模块若持 LLM 单例，应在下方显式 _register() 才能被热更新命中。
    """
    reloaded: list[str] = []
    for name, inst in _singletons.items():
        if hasattr(inst, "reload"):
            try:
                inst.reload()
                reloaded.append(name)
            except Exception as e:
                logger.warning("reload %s 失败: %s", name, e)

    from backend.modules.interaction import (
        cache_invalidate as invalidate_response_cache,
        char_data_cache_invalidate,
    )
    n = invalidate_response_cache()
    m = char_data_cache_invalidate()
    logger.info(
        "reload_all_llm: 重载 %s，清空 %d 条响应缓存 + %d 条角色数据缓存",
        reloaded, n, m,
    )
