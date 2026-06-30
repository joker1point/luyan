"""
上下文管理器 (Context Manager)

整合三层记忆：
1. 短期记忆（LangChain Window）- 即时对话上下文
2. 长期记忆（Mem0）- 跨会话语义记忆
3. 知识库（Cognee）- 外部知识 RAG

提供：
- 智能上下文组装（按重要性排序）
- Token 预算控制
- 自动压缩策略
- 上下文窗口管理

设计哲学：
- 分层加载：按"近期 → 重要 → 相关"优先级组装
- 预算控制：避免超出 LLM 上下文窗口
- 动态压缩：超长对话自动摘要压缩
"""

import logging
from typing import List, Dict, Any, Optional
from backend.memory.short_term import ShortTermMemory
from backend.memory.long_term import LongTermMemory
from backend.memory.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class ContextManager:
    """
    上下文管理器
    
    统一管理三层记忆，为 LLM 提供最优上下文
    """
    
    def __init__(
        self,
        character_id: str,
        user_id: Optional[str] = None,
        short_term_k: int = 10,
        max_tokens: int = 4000,
        long_term_limit: int = 5,
        knowledge_limit: int = 3
    ):
        """
        初始化上下文管理器
        
        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            short_term_k: 短期记忆保留轮数
            max_tokens: 最大 Token 预算
            long_term_limit: 长期记忆检索数量
            knowledge_limit: 知识库检索数量
        """
        self.character_id = character_id
        self.user_id = user_id
        self.max_tokens = max_tokens
        self.long_term_limit = long_term_limit
        self.knowledge_limit = knowledge_limit
        
        # 初始化三层记忆
        self.short_term = ShortTermMemory(k=short_term_k, session_id=character_id)
        self.long_term = LongTermMemory(
            user_id=user_id,
            agent_id=character_id
        )
        self.knowledge = KnowledgeBase(
            dataset_name=f"character_{character_id}"
        )
    
    def add_interaction(
        self,
        user_message: str,
        ai_message: str,
        promote_to_long_term: bool = True
    ) -> None:
        """
        添加一轮对话
        
        Args:
            user_message: 用户消息
            ai_message: AI 回复
            promote_to_long_term: 是否将重要内容提升到长期记忆
        """
        # 1. 添加到短期记忆
        self.short_term.add_user_message(user_message)
        self.short_term.add_ai_message(ai_message)
        
        # 2. 可选：提升到长期记忆
        if promote_to_long_term:
            content = f"用户: {user_message}\n角色: {ai_message}"
            self.long_term.add(
                content,
                metadata={
                    "type": "conversation",
                    "character_id": self.character_id,
                    "user_id": self.user_id
                }
            )
    
    def build_context(
        self,
        current_query: str,
        include_short_term: bool = True,
        include_long_term: bool = True,
        include_knowledge: bool = True
    ) -> Dict[str, Any]:
        """
        构建完整的 LLM 上下文
        
        按优先级组装：
        1. 当前查询（必选）
        2. 短期记忆（最近 K 轮）
        3. 长期记忆（语义相关）
        4. 知识库（RAG 检索）
        
        Args:
            current_query: 当前用户查询
            include_*: 各层是否包含
        
        Returns:
            上下文字典
        """
        context = {
            "query": current_query,
            "short_term": [],
            "long_term": [],
            "knowledge": [],
            "metadata": {
                "token_estimate": 0,
                "sources": []
            }
        }
        
        # 1. 短期记忆（必包含，除非显式排除）
        if include_short_term:
            short_messages = self.short_term.get_message_list()
            context["short_term"] = short_messages
            context["metadata"]["sources"].append("short_term")
        
        # 2. 长期记忆（语义检索）
        if include_long_term:
            long_memories = self.long_term.search(
                current_query, limit=self.long_term_limit
            )
            context["long_term"] = long_memories
            context["metadata"]["sources"].append("long_term")
        
        # 3. 知识库（RAG 检索）
        if include_knowledge:
            # [CTX-1 修复] 之前用 asyncio.run() 在事件循环中会报 RuntimeError;
            # 改为 KnowledgeBase.search_sync()，由 KB 自行处理运行中事件循环
            try:
                knowledge_results = self.knowledge.search_sync(
                    current_query, limit=self.knowledge_limit
                )
                context["knowledge"] = knowledge_results
                context["metadata"]["sources"].append("knowledge_base")
            except Exception as e:
                logger.warning(f"知识库检索失败: {e}")
                context["knowledge"] = []
        
        # 4. Token 估算（粗略：1 token ≈ 2 字符，中文）
        context["metadata"]["token_estimate"] = self._estimate_tokens(context)
        
        return context
    
    def _estimate_tokens(self, context: Dict[str, Any]) -> int:
        """粗略估算 Token 数"""
        total_chars = 0
        
        # 查询
        total_chars += len(context.get("query", ""))
        
        # 短期记忆
        for msg in context.get("short_term", []):
            total_chars += len(msg.get("content", ""))
        
        # 长期记忆
        for mem in context.get("long_term", []):
            content = mem.get("content", "") if isinstance(mem, dict) else str(mem)
            total_chars += len(content)
        
        # 知识库
        for doc in context.get("knowledge", []):
            content = doc.get("content", "") if isinstance(doc, dict) else str(doc)
            total_chars += len(content)
        
        # 中文 1 token ≈ 1.5 字符
        return int(total_chars / 1.5)
    
    def format_for_prompt(
        self,
        context: Dict[str, Any],
        template: str = "default"
    ) -> str:
        """
        将上下文格式化为 Prompt 文本
        
        Args:
            context: build_context 返回的字典
            template: 模板类型（default / minimal / detailed）
        """
        if template == "minimal":
            return self._format_minimal(context)
        elif template == "detailed":
            return self._format_detailed(context)
        else:
            return self._format_default(context)
    
    def _format_default(self, context: Dict[str, Any]) -> str:
        """默认格式"""
        parts = []
        
        # 短期对话历史
        if context["short_term"]:
            parts.append("【近期对话】")
            for msg in context["short_term"]:
                role = "用户" if msg["role"] == "user" else "角色"
                parts.append(f"{role}: {msg['content']}")
            parts.append("")
        
        # 相关长期记忆
        if context["long_term"]:
            parts.append("【相关记忆】")
            for mem in context["long_term"][:3]:
                content = mem.get("content", "") if isinstance(mem, dict) else str(mem)
                parts.append(f"- {content}")
            parts.append("")
        
        # 相关知识
        if context["knowledge"]:
            parts.append("【相关知识】")
            for doc in context["knowledge"][:2]:
                content = doc.get("content", "") if isinstance(doc, dict) else str(doc)
                parts.append(f"- {content[:200]}...")
            parts.append("")
        
        return "\n".join(parts)
    
    def _format_minimal(self, context: Dict[str, Any]) -> str:
        """最小化格式（节省 Token）"""
        parts = []
        if context["short_term"]:
            last_msg = context["short_term"][-1] if context["short_term"] else None
            if last_msg:
                parts.append(f"上一轮: {last_msg['content']}")
        return "\n".join(parts)
    
    def _format_detailed(self, context: Dict[str, Any]) -> str:
        """详细格式（包含元数据）"""
        parts = [f"[Token 估算: {context['metadata']['token_estimate']}]"]
        parts.append(self._format_default(context))
        return "\n".join(parts)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取记忆系统统计信息"""
        return {
            "character_id": self.character_id,
            "short_term_count": len(self.short_term),
            "long_term_count": len(self.long_term.get_all()),
            "max_tokens": self.max_tokens,
            "long_term_limit": self.long_term_limit,
            "knowledge_limit": self.knowledge_limit
        }
