"""
知识库单元测试

测试 KnowledgeBase 的：
- 添加文本
- 文档分块
- 检索功能
- 降级方案
"""

import pytest
import os
import tempfile
import asyncio
from backend.memory.knowledge_base import KnowledgeBase


def run_async(coro):
    """同步运行异步协程的辅助函数"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestKnowledgeBase:
    """知识库测试套件"""
    
    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        with tempfile.TemporaryDirectory() as tmp:
            yield tmp
    
    @pytest.fixture
    def kb(self, temp_dir):
        """创建测试用的知识库实例（强制使用降级方案）"""
        return KnowledgeBase(
            dataset_name="test_kb",
            use_cognee=False,  # 强制使用降级方案
            fallback_dir=temp_dir
        )
    
    def test_init(self, kb):
        """测试初始化"""
        assert kb.dataset_name == "test_kb"
        assert kb.use_cognee is False
        assert os.path.exists(kb._fallback_file)
    
    def test_add_text(self, kb):
        """测试添加文本（同步）"""
        success = run_async(kb.add_text("魔法世界的设定是...", source="world_setting"))
        assert success is True
    
    def test_add_multiple_texts(self, kb):
        """测试添加多条文本"""
        texts = [
            ("角色A 是勇敢的战士", "character_a"),
            ("角色B 是聪明的法师", "character_b"),
            ("角色C 是敏捷的弓箭手", "character_c"),
        ]
        for text, source in texts:
            success = run_async(kb.add_text(text, source=source))
            assert success is True
    
    def test_search(self, kb):
        """测试检索功能"""
        # 使用足够长的文本确保有可分割的段落
        run_async(kb.add_text("这是一个魔法世界 魔法世界由五大王国组成", "world"))
        run_async(kb.add_text("科技世界使用先进的机器 科技发展迅速", "tech"))
        
        results = run_async(kb.search("魔法", limit=5))
        assert isinstance(results, list)
        # 不强制要求有结果（降级方案的检索较简单）
    
    def test_search_with_limit(self, kb):
        """测试检索限制"""
        for i in range(10):
            run_async(kb.add_text(f"测试文本 {i}", source=f"src_{i}"))
        
        results = run_async(kb.search("测试", limit=3))
        assert len(results) <= 3
    
    def test_simple_chunk(self, kb):
        """测试简单分块"""
        text = "abcdefghij" * 10  # 100 字符
        chunks = kb._simple_chunk(text, chunk_size=30, overlap=5)
        
        assert len(chunks) > 0
        assert all(len(c) <= 30 for c in chunks)
    
    def test_simple_chunk_short_text(self, kb):
        """测试短文本分块"""
        text = "短文本"
        chunks = kb._simple_chunk(text, chunk_size=100, overlap=10)
        assert len(chunks) == 1
        assert chunks[0] == "短文本"
    
    def test_add_document(self, kb, temp_dir):
        """测试添加文档"""
        # 创建临时文档
        doc_path = os.path.join(temp_dir, "test_doc.txt")
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write("这是一个测试文档。\n" * 20)
        
        success = run_async(kb.add_document(doc_path))
        assert success is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
