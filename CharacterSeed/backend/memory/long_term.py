"""
长期记忆管理 (Long-term Memory)

基于 Mem0 实现：
- 语义检索：根据相关性而非时间检索记忆
- 智能压缩：自动提取关键信息
- 跨会话持久：支持长期存储
- 自我改进：自动处理冲突和更新

Mem0 核心优势：
- 相比 OpenAI 原生记忆，响应质量提升 26%
- Token 使用量降低 90%
- 推理延迟减少 91%

降级策略：
- 如果 mem0 未安装或初始化失败，自动降级到本地 JSON 存储
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# 尝试导入 mem0，失败则使用降级方案
try:
    from mem0 import Memory as Mem0Client
    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False
    logger.warning("mem0 未安装，将使用本地 JSON 存储作为降级方案")


class LongTermMemory:
    """
    长期记忆管理器
    
    使用 Mem0 实现语义化长期记忆，支持：
    - 添加记忆（自动提取关键信息）
    - 语义检索（基于相关性）
    - 跨会话共享
    - 自动冲突解决
    """
    
    def __init__(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        use_mem0: bool = True,
        fallback_path: str = "data/long_term_memory"
    ):
        """
        初始化长期记忆
        
        Args:
            user_id: 用户 ID（用于多用户隔离）
            agent_id: 角色/Agent ID（用于多角色隔离）
            use_mem0: 是否使用 mem0（False 则用本地存储）
            fallback_path: 降级方案的存储路径
        """
        self.user_id = user_id or "default_user"
        self.agent_id = agent_id or "default_agent"
        self.use_mem0 = use_mem0 and MEM0_AVAILABLE
        self.fallback_path = fallback_path
        
        if self.use_mem0:
            try:
                self.client = Mem0Client()
                logger.info("Mem0 长期记忆初始化成功")
            except Exception as e:
                logger.warning(f"Mem0 初始化失败，降级到本地存储: {e}")
                self.use_mem0 = False
                self._init_fallback()
        else:
            self._init_fallback()
    
    def _init_fallback(self) -> None:
        """初始化降级方案（本地 JSON 存储）"""
        os.makedirs(self.fallback_path, exist_ok=True)
        self._fallback_file = os.path.join(
            self.fallback_path, f"{self.user_id}_{self.agent_id}.json"
        )
        if not os.path.exists(self._fallback_file):
            with open(self._fallback_file, "w", encoding="utf-8") as f:
                json.dump({"memories": []}, f, ensure_ascii=False)
    
    def add(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        添加一条记忆
        
        Args:
            content: 记忆内容
            metadata: 元数据（如 importance, type 等）
        
        Returns:
            记忆 ID（mem0 模式）或 None
        """
        if self.use_mem0:
            try:
                result = self.client.add(
                    content,
                    user_id=self.user_id,
                    agent_id=self.agent_id,
                    metadata=metadata or {}
                )
                return result.get("id") if isinstance(result, dict) else None
            except Exception as e:
                logger.error(f"Mem0 添加记忆失败: {e}")
                return None
        else:
            # 降级到本地存储
            return self._add_fallback(content, metadata)
    
    def _add_fallback(
        self, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """降级方案：本地 JSON 存储"""
        with open(self._fallback_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        memory_id = f"mem_{len(data['memories']) + 1}_{int(datetime.now().timestamp())}"
        data["memories"].append({
            "id": memory_id,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat()
        })
        
        with open(self._fallback_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return memory_id
    
    def search(
        self,
        query: str,
        limit: int = 5,
        threshold: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        语义检索记忆
        
        Args:
            query: 查询文本
            limit: 返回数量
            threshold: 相关性阈值（0-1）
        
        Returns:
            相关记忆列表
        """
        if self.use_mem0:
            try:
                results = self.client.search(
                    query=query,
                    user_id=self.user_id,
                    agent_id=self.agent_id,
                    limit=limit
                )
                return results if isinstance(results, list) else []
            except Exception as e:
                logger.error(f"Mem0 检索失败: {e}")
                return self._search_fallback(query, limit)
        else:
            return self._search_fallback(query, limit)
    
    def _tokenize(self, text: str) -> List[str]:
        """
        文本分词：支持中文按字切分 + 英文按词切分
        - 英文/数字按空格和标点分词
        - 中文按 1-2 字滑窗切分（兼容中英混合）
        """
        import re
        text = text.lower().strip()
        # 提取所有连续中文片段
        tokens = []
        # 1) 英文/数字 token
        en_tokens = re.findall(r"[a-z0-9]+", text)
        tokens.extend(en_tokens)
        # 2) 中文字符 1-gram 和 2-gram
        cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
        tokens.extend(cn_chars)
        for i in range(len(cn_chars) - 1):
            tokens.append(cn_chars[i] + cn_chars[i + 1])
        return tokens

    def _search_fallback(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """
        降级方案：基于分词的关键词匹配
        支持中文按字 + 英文按词的混合匹配
        """
        with open(self._fallback_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        query_tokens = set(self._tokenize(query))
        if not query_tokens:
            return []

        scored_memories = []
        for mem in data["memories"]:
            content_tokens = set(self._tokenize(mem["content"]))
            if not content_tokens:
                continue
            overlap = len(query_tokens & content_tokens)
            if overlap > 0:
                # Jaccard 相似度，避免短查询分母过小
                score = overlap / len(query_tokens | content_tokens)
                scored_memories.append({
                    **mem,
                    "score": round(score, 4)
                })

        scored_memories.sort(key=lambda x: x["score"], reverse=True)
        return scored_memories[:limit]
    
    def get_all(self) -> List[Dict[str, Any]]:
        """获取所有记忆"""
        if self.use_mem0:
            try:
                results = self.client.get_all(
                    user_id=self.user_id,
                    agent_id=self.agent_id
                )
                return results if isinstance(results, list) else []
            except Exception as e:
                logger.error(f"Mem0 获取所有记忆失败: {e}")
                return []
        else:
            with open(self._fallback_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("memories", [])
    
    def delete(self, memory_id: str) -> bool:
        """删除指定记忆"""
        if self.use_mem0:
            try:
                self.client.delete(memory_id)
                return True
            except Exception as e:
                logger.error(f"Mem0 删除失败: {e}")
                return False
        else:
            with open(self._fallback_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            data["memories"] = [m for m in data["memories"] if m["id"] != memory_id]
            
            with open(self._fallback_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
