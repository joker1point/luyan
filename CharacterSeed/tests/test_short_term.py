"""
短期记忆单元测试

测试 ShortTermMemory 的：
- 基本增删改查
- 窗口大小限制
- 格式化输出
- 清空操作
"""

import pytest
from backend.memory.short_term import ShortTermMemory


class TestShortTermMemory:
    """短期记忆测试套件"""
    
    def test_init_default(self):
        """测试默认初始化"""
        stm = ShortTermMemory()
        assert stm.k == 10
        assert stm.session_id == "default"
        assert len(stm) == 0
    
    def test_init_custom(self):
        """测试自定义参数初始化"""
        stm = ShortTermMemory(k=5, session_id="test_session")
        assert stm.k == 5
        assert stm.session_id == "test_session"
    
    def test_add_single_message(self):
        """测试添加单条消息"""
        stm = ShortTermMemory(k=5)
        stm.add_user_message("你好")
        assert len(stm) == 1
    
    def test_add_interaction(self):
        """测试添加一轮对话"""
        stm = ShortTermMemory(k=5)
        stm.add_user_message("你好")
        stm.add_ai_message("你好！")
        assert len(stm) == 2
    
    def test_window_limit(self):
        """测试窗口大小限制"""
        stm = ShortTermMemory(k=3)  # 只保留 3 轮 = 6 条消息
        
        # 添加 5 轮对话
        for i in range(5):
            stm.add_user_message(f"用户消息 {i}")
            stm.add_ai_message(f"AI 回复 {i}")
        
        # 应该只保留最近 3 轮
        assert len(stm) == 6
        
        # 验证最早的消息被淘汰
        messages = stm.get_message_list()
        assert messages[0]["content"] == "用户消息 2"
        assert messages[-1]["content"] == "AI 回复 4"
    
    def test_get_message_list(self):
        """测试获取消息列表（OpenAI 格式）"""
        stm = ShortTermMemory(k=5)
        stm.add_user_message("你好")
        stm.add_ai_message("你好！")
        
        messages = stm.get_message_list()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "你好"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "你好！"
    
    def test_get_context_text(self):
        """测试获取格式化文本"""
        stm = ShortTermMemory(k=5)
        stm.add_user_message("你好")
        stm.add_ai_message("你好！")
        
        text = stm.get_context_text()
        assert "[用户]: 你好" in text
        assert "[AI]: 你好！" in text
    
    def test_get_context_text_empty(self):
        """测试空记忆的文本输出"""
        stm = ShortTermMemory(k=5)
        text = stm.get_context_text()
        assert "暂无对话历史" in text
    
    def test_clear(self):
        """测试清空操作"""
        stm = ShortTermMemory(k=5)
        stm.add_user_message("你好")
        stm.add_ai_message("你好！")
        assert len(stm) == 2
        
        stm.clear()
        assert len(stm) == 0
    
    def test_message_ordering(self):
        """测试消息顺序保持"""
        stm = ShortTermMemory(k=10)
        messages = ["第一", "第二", "第三", "第四"]
        for msg in messages:
            stm.add_user_message(msg)
        
        retrieved = [m["content"] for m in stm.get_message_list()]
        assert retrieved == messages


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
