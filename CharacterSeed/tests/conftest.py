"""pytest 全局配置：确保项目根目录在 sys.path 中"""
import sys
from pathlib import Path

# 将项目根目录（tests/../）加入模块搜索路径
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
