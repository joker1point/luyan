"""
记忆系统模块 - 整合短期记忆、长期记忆、知识库

三层架构：
- 短期记忆 (Short-term)：LangChain Window Memory，保留最近 K 轮对话
- 长期记忆 (Long-term)：Mem0 语义记忆，跨会话持久化
- 知识库 (RAG)：Cognee 知识图谱，存储角色世界设定和外部文档
"""

from backend.memory.short_term import ShortTermMemory
from backend.memory.long_term import LongTermMemory
from backend.memory.knowledge_base import KnowledgeBase
from backend.memory.context_manager import ContextManager

__all__ = [
    "ShortTermMemory",
    "LongTermMemory",
    "KnowledgeBase",
    "ContextManager",
]
