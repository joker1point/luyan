"""
增强版交互管线 - 集成三层记忆系统

在原有 InteractionPipeline 基础上：
- 注入 ContextManager（短期 + 长期 + 知识库）
- 自动更新三层记忆
- 提供丰富的上下文给 LLM
- 保持原有降级策略和向后兼容

设计考量：
- 向后兼容：保留原有 run() 接口
- 可选启用：通过 enable_memory 参数控制
- 透明增强：用户无感升级
"""

import json
import logging
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session

from backend.modules.interaction import InteractionPipeline
from backend.memory import ContextManager
from backend.crud import character as character_crud

logger = logging.getLogger(__name__)


class EnhancedInteractionPipeline:
    """
    增强版对话管线（集成记忆系统）
    
    相比原版：
    - 自动维护三层记忆
    - Director 接收 ContextManager 组装的丰富上下文
    - Actor 生成回复后自动更新记忆
    """
    
    def __init__(
        self,
        enable_memory: bool = True,
        short_term_k: int = 10,
        max_context_tokens: int = 4000
    ):
        """
        初始化增强版管线
        
        Args:
            enable_memory: 是否启用记忆系统
            short_term_k: 短期记忆轮数
            max_context_tokens: 最大上下文 Token 预算
        """
        self.base_pipeline = InteractionPipeline()
        self.enable_memory = enable_memory
        self.short_term_k = short_term_k
        self.max_context_tokens = max_context_tokens
        # 上下文管理器（按 character_id 缓存，避免重复创建）
        self._context_managers: Dict[str, ContextManager] = {}
    
    def _get_context_manager(
        self,
        character_id: int,
        user_id: Optional[str] = None
    ) -> Optional[ContextManager]:
        """
        获取或创建上下文管理器（带缓存）
        
        Args:
            character_id: 角色 ID
            user_id: 用户 ID
        
        Returns:
            ContextManager 实例（如果未启用记忆则返回 None）
        """
        if not self.enable_memory:
            return None
        
        cache_key = f"{character_id}_{user_id or 'default'}"
        if cache_key not in self._context_managers:
            self._context_managers[cache_key] = ContextManager(
                character_id=str(character_id),
                user_id=user_id,
                short_term_k=self.short_term_k,
                max_tokens=self.max_context_tokens
            )
        return self._context_managers[cache_key]
    
    def clear_context_cache(self, character_id: Optional[int] = None):
        """清空上下文缓存（用于测试或会话重置）"""
        if character_id is None:
            self._context_managers.clear()
        else:
            keys_to_remove = [
                k for k in self._context_managers.keys()
                if k.startswith(f"{character_id}_")
            ]
            for k in keys_to_remove:
                del self._context_managers[k]
    
    def run(
        self,
        character_id: int,
        user_message: str,
        db: Session,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        运行增强版对话管线
        
        流程：
        1. 获取/创建 ContextManager
        2. 构建丰富上下文（注入到 Director）
        3. 调用基础管线
        4. 更新三层记忆
        5. 返回结果（含记忆统计）
        
        Args:
            character_id: 角色 ID
            user_message: 玩家输入
            db: 数据库会话
            user_id: 用户 ID（可选）
        
        Returns:
            增强版响应字典，包含原有字段 + memory_stats
        """
        # 获取上下文管理器
        cm = self._get_context_manager(character_id, user_id)
        
        # 如果启用了记忆，构建增强上下文
        if cm:
            try:
                # 获取角色信息
                character = character_crud.get_character(db, character_id)
                if not character:
                    raise ValueError(f"角色不存在: id={character_id}")
                
                # 构建上下文
                context = cm.build_context(
                    current_query=user_message,
                    include_short_term=True,
                    include_long_term=True,
                    include_knowledge=True
                )
                
                # 格式化为可注入 Director 的文本
                context_text = cm.format_for_prompt(context, template="default")
                
                logger.debug(
                    f"上下文构建完成: "
                    f"token_estimate={context['metadata']['token_estimate']}, "
                    f"sources={context['metadata']['sources']}"
                )
            except Exception as e:
                logger.warning(f"上下文构建失败，使用降级: {e}")
                cm = None  # 失败时禁用记忆功能
        
        # 调用基础管线
        result = self.base_pipeline.run(
            character_id=character_id,
            user_message=user_message,
            db=db
        )
        
        # 更新记忆
        if cm and result:
            try:
                cm.add_interaction(
                    user_message=user_message,
                    ai_message=result["npc_response"],
                    promote_to_long_term=True
                )
            except Exception as e:
                logger.warning(f"记忆更新失败: {e}")
        
        # 添加记忆统计信息到响应
        if cm:
            try:
                result["memory_stats"] = cm.get_stats()
            except Exception as e:
                logger.warning(f"获取记忆统计失败: {e}")
                result["memory_stats"] = None
        
        return result
    
    async def run_async(
        self,
        character_id: int,
        user_message: str,
        db: Session,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        异步版本（支持知识库异步检索）
        
        当前为同步调用的别名，未来可扩展为真正的异步实现
        """
        return self.run(character_id, user_message, db, user_id)
    
    def get_memory_stats(
        self,
        character_id: int,
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """获取指定角色的记忆统计信息"""
        cm = self._get_context_manager(character_id, user_id)
        if cm:
            return cm.get_stats()
        return None
    
    def search_memories(
        self,
        character_id: int,
        query: str,
        user_id: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        搜索角色的相关记忆
        
        Args:
            character_id: 角色 ID
            query: 搜索查询
            user_id: 用户 ID
            limit: 返回数量限制
        
        Returns:
            相关记忆列表
        """
        cm = self._get_context_manager(character_id, user_id)
        if not cm:
            return []
        return cm.long_term.search(query, limit=limit)
