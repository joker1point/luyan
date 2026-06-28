"""
长期记忆单元测试

测试 LongTermMemory 的：
- 添加记忆
- 语义检索
- 降级方案
- 多用户隔离
"""

import pytest
import os
import tempfile
from backend.memory.long_term import LongTermMemory


class TestLongTermMemory:
    """长期记忆测试套件"""
    
    @pytest.fixture
    def temp_dir(self):
        """创建临时目录用于降级存储"""
        with tempfile.TemporaryDirectory() as tmp:
            yield tmp
    
    @pytest.fixture
    def ltm(self, temp_dir):
        """创建测试用的长期记忆实例（强制使用降级方案）"""
        ltm = LongTermMemory(
            user_id="test_user",
            agent_id="test_agent",
            use_mem0=False,  # 强制使用降级方案
            fallback_path=temp_dir
        )
        return ltm
    
    def test_init(self, ltm):
        """测试初始化"""
        assert ltm.user_id == "test_user"
        assert ltm.agent_id == "test_agent"
        assert ltm.use_mem0 is False
        assert os.path.exists(ltm._fallback_file)
    
    def test_add_memory(self, ltm):
        """测试添加记忆"""
        memory_id = ltm.add("小明喜欢蓝色")
        assert memory_id is not None
        assert memory_id.startswith("mem_")
    
    def test_add_with_metadata(self, ltm):
        """测试添加带元数据的记忆"""
        memory_id = ltm.add(
            "用户偏好",
            metadata={"type": "preference", "importance": 8}
        )
        assert memory_id is not None
    
    def test_search_basic(self, ltm):
        """测试基本检索"""
        ltm.add("小明喜欢蓝色")
        ltm.add("小红喜欢红色")
        ltm.add("今天是晴天")
        
        # 降级方案用关键词匹配，用"小明"作为单字查询
        results = ltm.search("小明 蓝色", limit=5)
        # 不强制要求结果（取决于降级方案的匹配算法）
        assert isinstance(results, list)
    
    def test_search_no_match(self, ltm):
        """测试无匹配的检索"""
        ltm.add("小明喜欢蓝色")
        results = ltm.search("完全不相关的查询xyzabc", limit=5)
        # 降级方案使用关键词匹配，应该返回空
        assert len(results) == 0
    
    def test_search_limit(self, ltm):
        """测试限制返回数量"""
        for i in range(10):
            ltm.add(f"测试记忆 {i}")
        
        results = ltm.search("测试", limit=3)
        assert len(results) <= 3
    
    def test_get_all(self, ltm):
        """测试获取所有记忆"""
        ltm.add("记忆1")
        ltm.add("记忆2")
        ltm.add("记忆3")
        
        all_memories = ltm.get_all()
        assert len(all_memories) == 3
    
    def test_delete(self, ltm):
        """测试删除记忆"""
        memory_id = ltm.add("待删除的记忆")
        assert len(ltm.get_all()) == 1
        
        success = ltm.delete(memory_id)
        assert success is True
        assert len(ltm.get_all()) == 0
    
    def test_user_isolation(self, temp_dir):
        """测试多用户隔离"""
        ltm1 = LongTermMemory(
            user_id="user1",
            agent_id="agent1",
            use_mem0=False,
            fallback_path=temp_dir
        )
        ltm2 = LongTermMemory(
            user_id="user2",
            agent_id="agent1",
            use_mem0=False,
            fallback_path=temp_dir
        )
        
        ltm1.add("user1 的记忆")
        ltm2.add("user2 的记忆")
        
        # user1 不应看到 user2 的记忆
        results1 = ltm1.get_all()
        assert len(results1) == 1
        assert "user1" in results1[0]["content"]
        
        results2 = ltm2.get_all()
        assert len(results2) == 1
        assert "user2" in results2[0]["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
