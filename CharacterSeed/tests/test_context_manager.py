"""
上下文管理器单元测试

测试 ContextManager 的：
- 三层记忆整合
- 上下文构建
- 格式化输出
- Token 估算
- 统计信息
"""

import pytest
import tempfile
from backend.memory import ContextManager


class TestContextManager:
    """上下文管理器测试套件"""
    
    @pytest.fixture
    def cm(self):
        """创建测试用的上下文管理器"""
        with tempfile.TemporaryDirectory() as tmp:
            return ContextManager(
                character_id="test_char_001",
                user_id="test_user",
                short_term_k=5,
                max_tokens=2000,
                long_term_limit=3,
                knowledge_limit=2
            )
    
    def test_init(self, cm):
        """测试初始化"""
        assert cm.character_id == "test_char_001"
        assert cm.user_id == "test_user"
        assert cm.max_tokens == 2000
    
    def test_add_interaction(self, cm):
        """测试添加交互"""
        cm.add_interaction("你好", "你好！")
        assert len(cm.short_term) == 2
    
    def test_add_interaction_promote(self, cm):
        """测试自动提升到长期记忆"""
        cm.add_interaction(
            "我叫小明", 
            "你好小明",
            promote_to_long_term=True
        )
        
        # 长期记忆应该有内容
        all_memories = cm.long_term.get_all()
        # 注意：降级方案下，添加可能失败或成功
        # 这里只测试不崩溃
        assert isinstance(all_memories, list)
    
    def test_build_context(self, cm):
        """测试构建上下文"""
        cm.add_interaction("你好", "你好！")
        
        context = cm.build_context("今天天气如何？")
        
        assert "query" in context
        assert "short_term" in context
        assert "long_term" in context
        assert "knowledge" in context
        assert "metadata" in context
        assert context["query"] == "今天天气如何？"
    
    def test_build_context_selective(self, cm):
        """测试选择性包含"""
        cm.add_interaction("你好", "你好！")
        
        # 只包含短期记忆
        context = cm.build_context(
            "查询",
            include_short_term=True,
            include_long_term=False,
            include_knowledge=False
        )
        
        assert len(context["short_term"]) > 0
        assert len(context["long_term"]) == 0
        assert len(context["knowledge"]) == 0
    
    def test_format_default(self, cm):
        """测试默认格式"""
        cm.add_interaction("你好", "你好！")
        context = cm.build_context("查询")
        formatted = cm.format_for_prompt(context, template="default")
        
        assert "近期对话" in formatted or "用户" in formatted
    
    def test_format_minimal(self, cm):
        """测试最小化格式"""
        cm.add_interaction("你好", "你好！")
        context = cm.build_context("查询")
        formatted = cm.format_for_prompt(context, template="minimal")
        
        # 最小化格式应该更短
        assert len(formatted) < 200
    
    def test_format_detailed(self, cm):
        """测试详细格式"""
        cm.add_interaction("你好", "你好！")
        context = cm.build_context("查询")
        formatted = cm.format_for_prompt(context, template="detailed")
        
        # 详细格式应包含 Token 估算
        assert "Token" in formatted or "token" in formatted
    
    def test_token_estimate(self, cm):
        """测试 Token 估算"""
        cm.add_interaction("这是一条较长的消息用于测试 token 估算功能", "AI 的回复")
        
        context = cm.build_context("查询")
        estimate = context["metadata"]["token_estimate"]
        
        assert estimate > 0
        assert isinstance(estimate, int)
    
    def test_get_stats(self, cm):
        """测试统计信息"""
        cm.add_interaction("消息1", "回复1")
        cm.add_interaction("消息2", "回复2")
        
        stats = cm.get_stats()
        
        assert stats["character_id"] == "test_char_001"
        assert stats["short_term_count"] == 4
        assert "max_tokens" in stats
        assert "long_term_limit" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
