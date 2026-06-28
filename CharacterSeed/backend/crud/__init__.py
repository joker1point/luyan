from backend.crud import character
from backend.crud import conversation
from backend.crud import memory
from backend.crud import growth
from backend.crud import event
from backend.crud import world

# [v3.x-fix] scene / scene_change 模块依赖 Scene 模型（尚未在 models.py 中定义），
# 使用惰性导入避免阻塞整个 crud 包的加载。
try:
    from backend.crud import scene
except ImportError:
    scene = None
try:
    from backend.crud import scene_change
except ImportError:
    scene_change = None

__all__ = [
    "character", "conversation", "memory", "growth", "event",
    "world", "scene", "scene_change",
]
