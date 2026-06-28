"""
增强版 InteractionPipeline 集成测试

测试 EnhancedInteractionPipeline 的：
- 与基础管线的兼容性
- 三层记忆自动更新
- 记忆统计返回
- 缓存管理
"""

import pytest
import tempfile
from unittest.mock import Mock, patch, MagicMock
from backend.modules.enhanced_interaction import EnhancedInteractionPipeline


class TestEnhancedInteractionPipeline:
    """增强管线集成测试"""
    
    @pytest.fixture
    def pipeline(self):
        """创建测试用的增强管线"""
        return EnhancedInteractionPipeline(
            enable_memory=True,
            short_term_k=5,
            max_context_tokens=2000
        )
    
    def test_init(self, pipeline):
        """测试初始化"""
        assert pipeline.enable_memory is True
        assert pipeline.short_term_k == 5
        assert pipeline.max_context_tokens == 2000
        assert pipeline._context_managers == {}
    
    def test_get_context_manager(self, pipeline):
        """测试获取上下文管理器"""
        cm = pipeline._get_context_manager(
            character_id=1,
            user_id="user1"
        )
        
        assert cm is not None
        assert "1_user1" in pipeline._context_managers
    
    def test_get_context_manager_cached(self, pipeline):
        """测试上下文管理器缓存"""
        cm1 = pipeline._get_context_manager(1, "user1")
        cm2 = pipeline._get_context_manager(1, "user1")
        
        # 同一个角色+用户应该返回同一个实例
        assert cm1 is cm2
    
    def test_get_context_manager_different_users(self, pipeline):
        """测试不同用户的上下文隔离"""
        cm1 = pipeline._get_context_manager(1, "user1")
        cm2 = pipeline._get_context_manager(1, "user2")
        
        assert cm1 is not cm2
    
    def test_clear_cache_specific(self, pipeline):
        """测试清除指定角色缓存"""
        pipeline._get_context_manager(1, "user1")
        pipeline._get_context_manager(2, "user1")
        
        assert len(pipeline._context_managers) == 2
        
        pipeline.clear_context_cache(character_id=1)
        
        # 应该只清除角色 1 的缓存
        assert len(pipeline._context_managers) == 1
        assert "2_user1" in pipeline._context_managers
    
    def test_clear_cache_all(self, pipeline):
        """测试清除所有缓存"""
        pipeline._get_context_manager(1, "user1")
        pipeline._get_context_manager(2, "user2")
        
        assert len(pipeline._context_managers) == 2
        
        pipeline.clear_context_cache()
        
        assert len(pipeline._context_managers) == 0
    
    def test_get_memory_stats(self, pipeline):
        """测试获取记忆统计"""
        # 先创建一个 context manager
        cm = pipeline._get_context_manager(1, "user1")
        cm.add_interaction("测试", "回复")
        
        stats = pipeline.get_memory_stats(1, "user1")
        
        assert stats is not None
        assert stats["character_id"] == "1"
        assert stats["short_term_count"] == 2
    
    def test_search_memories(self, pipeline):
        """测试记忆搜索"""
        cm = pipeline._get_context_manager(1, "user1")
        cm.long_term.add("小明喜欢蓝色", metadata={"type": "preference"})
        
        results = pipeline.search_memories(1, "小明的颜色", user_id="user1")
        
        # 应该有结果
        assert isinstance(results, list)
    
    def test_disabled_memory(self):
        """测试禁用记忆系统"""
        pipeline = EnhancedInteractionPipeline(enable_memory=False)
        
        cm = pipeline._get_context_manager(1, "user1")
        assert cm is None
        
        stats = pipeline.get_memory_stats(1, "user1")
        assert stats is None


class TestEnhancedInteractionPipelineIntegration:
    """端到端集成测试（需要数据库）"""
    
    @pytest.fixture
    def pipeline_with_db(self):
        """创建带数据库的测试管线"""
        # 使用内存数据库
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.database import Base
        
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        TestingSessionLocal = sessionmaker(bind=engine)
        
        pipeline = EnhancedInteractionPipeline(enable_memory=True)
        db = TestingSessionLocal()
        
        yield pipeline, db
        
        db.close()
    
    def test_run_with_nonexistent_character(self, pipeline_with_db):
        """测试角色不存在时的错误处理"""
        pipeline, db = pipeline_with_db
        
        with pytest.raises(ValueError):
            pipeline.run(
                character_id=99999,  # 不存在的角色
                user_message="你好",
                db=db
            )
    
    def test_run_basic_flow(self, pipeline_with_db):
        """测试基本运行流程（无 LLM 调用）"""
        pipeline, db = pipeline_with_db
        
        # 创建测试角色
        from backend.crud import character as character_crud
        character = character_crud.create_character(
            db=db,
            name="测试角色",
            description="测试",
            personality={"optimism": 50, "courage": 50},
            current_state={"location": "测试地点"}
        )
        
        # Mock 基础管线
        with patch.object(
            pipeline.base_pipeline, 'run'
        ) as mock_run:
            mock_run.return_value = {
                "id": 1,
                "character_id": character.id,
                "user_input": "你好",
                "npc_response": "你好！很高兴认识你",
                "emotion": "开心",
                "action": "微笑",
                "expression": "友善",
                "director_raw": "{}",
                "actor_raw": "{}",
                "timestamp": None
            }
            
            result = pipeline.run(
                character_id=character.id,
                user_message="你好",
                db=db,
                user_id="test_user"
            )
            
            # 验证基本字段
            assert result["user_input"] == "你好"
            assert result["npc_response"] == "你好！很高兴认识你"
            
            # 验证记忆统计已添加
            assert "memory_stats" in result
            assert result["memory_stats"] is not None
            assert result["memory_stats"]["short_term_count"] == 2  # 一轮对话 = 2 条


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
