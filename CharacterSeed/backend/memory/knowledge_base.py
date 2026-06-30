"""
知识库管理 (Knowledge Base / RAG)

基于 Cognee 实现：
- 知识图谱 + 向量存储 双重索引
- 自动提取实体和关系
- 语义搜索 + 图遍历
- 支持 30+ 数据源

降级策略：
- 如果 cognee 未安装，使用简单的文件存储 + 关键词检索
"""

import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# 尝试导入 cognee
try:
    import cognee
    COGNEE_AVAILABLE = True
except ImportError:
    COGNEE_AVAILABLE = False
    logger.warning("cognee 未安装，将使用简单文件存储作为降级方案")


class KnowledgeBase:
    """
    知识库管理器
    
    使用 Cognee 实现结构化知识存储：
    - 文档分块
    - 实体关系抽取
    - 知识图谱构建
    - 语义检索
    """
    
    def __init__(
        self,
        dataset_name: str = "character_knowledge",
        use_cognee: bool = True,
        fallback_dir: str = "data/knowledge_base"
    ):
        """
        初始化知识库
        
        Args:
            dataset_name: 数据集名称（用于多角色隔离）
            use_cognee: 是否使用 cognee
            fallback_dir: 降级方案的存储目录
        """
        self.dataset_name = dataset_name
        self.use_cognee = use_cognee and COGNEE_AVAILABLE
        self.fallback_dir = fallback_dir
        
        if self.use_cognee:
            try:
                # cognee 初始化
                logger.info(f"Cognee 知识库初始化成功: {dataset_name}")
            except Exception as e:
                logger.warning(f"Cognee 初始化失败，降级到本地存储: {e}")
                self.use_cognee = False
                self._init_fallback()
        else:
            self._init_fallback()
    
    def _init_fallback(self) -> None:
        """初始化降级方案（本地文件存储）"""
        os.makedirs(self.fallback_dir, exist_ok=True)
        self._fallback_file = os.path.join(
            self.fallback_dir, f"{self.dataset_name}.txt"
        )
        if not os.path.exists(self._fallback_file):
            with open(self._fallback_file, "w", encoding="utf-8") as f:
                f.write("")
    
    async def add_text(
        self,
        text: str,
        source: Optional[str] = None
    ) -> bool:
        """
        添加文本到知识库
        
        Args:
            text: 文本内容
            source: 来源标识（如 "character_creation", "world_setting"）
        
        Returns:
            是否成功
        """
        if self.use_cognee:
            try:
                await cognee.add(text, dataset_name=self.dataset_name)
                await cognee.cognify(datasets=[self.dataset_name])
                return True
            except Exception as e:
                logger.error(f"Cognee 添加文本失败: {e}")
                return self._add_text_fallback(text, source)
        else:
            return self._add_text_fallback(text, source)
    
    def _add_text_fallback(self, text: str, source: Optional[str] = None) -> bool:
        """降级方案：追加到文件"""
        try:
            with open(self._fallback_file, "a", encoding="utf-8") as f:
                f.write(f"\n--- {source or 'unknown'} ---\n{text}\n")
            return True
        except Exception as e:
            logger.error(f"本地知识库写入失败: {e}")
            return False
    
    async def add_document(
        self,
        file_path: str,
        chunk_size: int = 512,
        chunk_overlap: int = 50
    ) -> bool:
        """
        添加文档到知识库（自动分块）
        
        Args:
            file_path: 文档路径
            chunk_size: 分块大小（字符数）
            chunk_overlap: 块之间的重叠字符数
        """
        if self.use_cognee:
            try:
                # cognee 支持多种数据源
                await cognee.add(file_path, dataset_name=self.dataset_name)
                await cognee.cognify(datasets=[self.dataset_name])
                return True
            except Exception as e:
                logger.error(f"Cognee 添加文档失败: {e}")
                return False
        else:
            # 降级方案：简单分块
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                chunks = self._simple_chunk(content, chunk_size, chunk_overlap)
                for i, chunk in enumerate(chunks):
                    self._add_text_fallback(
                        chunk, source=f"{file_path}_chunk_{i}"
                    )
                return True
            except Exception as e:
                logger.error(f"本地文档处理失败: {e}")
                return False
    
    def _simple_chunk(
        self, text: str, chunk_size: int, overlap: int
    ) -> List[str]:
        """简单的滑动窗口分块"""
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            start = end - overlap
        return chunks
    
    async def search(
        self,
        query: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        语义检索知识库
        
        Args:
            query: 查询文本
            limit: 返回数量
        """
        if self.use_cognee:
            try:
                results = await cognee.search(
                    query_text=query,
                    datasets=[self.dataset_name]
                )
                return results if isinstance(results, list) else []
            except Exception as e:
                logger.error(f"Cognee 检索失败: {e}")
                return self._search_fallback(query, limit)
        else:
            return self._search_fallback(query, limit)
    
    def _tokenize(self, text: str) -> List[str]:
        """
        文本分词：支持中文按字 + 英文按词
        """
        import re
        text = text.lower().strip()
        tokens = []
        en_tokens = re.findall(r"[a-z0-9]+", text)
        tokens.extend(en_tokens)
        cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
        tokens.extend(cn_chars)
        for i in range(len(cn_chars) - 1):
            tokens.append(cn_chars[i] + cn_chars[i + 1])
        return tokens

    def search_sync(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        同步检索知识库（用于 sync 上下文调用 asyncio.run 会被事件循环拒绝的场景）。

        优先用同步包装调用 async search；如处于运行中事件循环则降级到本地 _search_fallback。
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # [CTX-1 修复] 在事件循环中 asyncio.run 会抛 RuntimeError，直接走 fallback
                return self._search_fallback(query, limit)
            return loop.run_until_complete(self.search(query, limit))
        except RuntimeError:
            return self._search_fallback(query, limit)

    def _search_fallback(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """降级方案：基于分词的关键词匹配"""
        try:
            with open(self._fallback_file, "r", encoding="utf-8") as f:
                content = f.read()

            # 按段落分割（每段以 --- source --- 标记）
            paragraphs = [p for p in content.split("---") if p.strip()]
            query_tokens = set(self._tokenize(query))
            if not query_tokens:
                return []

            scored = []
            for i, para in enumerate(paragraphs):
                para_tokens = set(self._tokenize(para))
                if not para_tokens:
                    continue
                overlap = len(query_tokens & para_tokens)
                if overlap > 0:
                    score = overlap / len(query_tokens | para_tokens)
                    scored.append({
                        "text": para.strip(),
                        "score": round(score, 4),
                        "chunk_id": i
                    })

            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:limit]
        except Exception as e:
            logger.error(f"本地检索失败: {e}")
            return []
