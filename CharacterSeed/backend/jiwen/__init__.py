"""
jiwen 引擎 — Python 移植版入口

详见 jiwen_core.py
"""
from backend.jiwen.jiwen_core import (
    JiwenEngine,
    JiwenStateSnapshot,
    create_jiwen,
    DEFAULT_RATES,
    DEFAULT_THRESHOLDS,
    DEFAULT_AXES,
)
from backend.jiwen.jiwen_manager import (
    JiwenManager,
    get_jiwen_manager,
)
from backend.jiwen.jiwen_scheduler import (
    JiwenBackgroundScheduler,
    get_scheduler,
    start_scheduler,
    stop_scheduler,
)

__all__ = [
    "JiwenEngine",
    "JiwenStateSnapshot",
    "create_jiwen",
    "JiwenManager",
    "get_jiwen_manager",
    "JiwenBackgroundScheduler",
    "get_scheduler",
    "start_scheduler",
    "stop_scheduler",
    "DEFAULT_RATES",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_AXES",
]
