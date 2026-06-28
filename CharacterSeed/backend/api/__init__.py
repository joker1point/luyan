"""
API 路由模块

注意：子模块用 `from . import xxx_router` 显式相对导入，
不要写 `from backend.api import xxx_router`（自引用不触发子模块 import）。
"""
from . import (
    character_router,
    chat_router,
    session_router,
    growth_router,
    event_router,
    character_memory_router,
    performance_router,
    llm_router,
    logs_router,
    jiwen_router,
    world_router,
)
