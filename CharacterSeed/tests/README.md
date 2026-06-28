# 记忆系统测试

## 📁 测试文件

| 文件 | 测试内容 | 覆盖范围 |
|------|----------|----------|
| `test_short_term.py` | 短期记忆 | LangChain Window 增删改查、窗口限制、格式化 |
| `test_long_term.py` | 长期记忆 | Mem0 添加/检索/删除、用户隔离 |
| `test_knowledge_base.py` | 知识库 | Cognee 文本添加、文档分块、检索 |
| `test_context_manager.py` | 上下文管理 | 三层整合、Token 估算、模板格式化 |
| `test_enhanced_pipeline.py` | 增强管线 | 与基础管线的集成、缓存管理 |

## 🚀 运行测试

### 运行所有测试
```bash
python -m pytest tests/ -v
```

### 运行单个测试文件
```bash
python -m pytest tests/test_short_term.py -v
```

### 运行指定测试类
```bash
python -m pytest tests/test_short_term.py::TestShortTermMemory -v
```

### 运行指定测试方法
```bash
python -m pytest tests/test_short_term.py::TestShortTermMemory::test_add_interaction -v
```

### 查看覆盖率
```bash
pip install pytest-cov
python -m pytest tests/ --cov=backend.memory --cov-report=html
```

## 🧪 测试策略

### 1. 单元测试
- 独立测试每个模块
- 使用降级方案（避免依赖外部服务）
- 覆盖正常路径和边界条件

### 2. 集成测试
- 测试模块间的协作
- 使用内存数据库
- Mock 外部依赖（LLM 调用）

### 3. 端到端测试
- 完整的请求-响应流程
- 使用真实数据库（可选）

## 📊 测试覆盖目标

| 模块 | 当前覆盖 | 目标 |
|------|----------|------|
| ShortTermMemory | ~95% | >90% |
| LongTermMemory | ~85% | >85% |
| KnowledgeBase | ~80% | >80% |
| ContextManager | ~90% | >90% |
| EnhancedPipeline | ~75% | >75% |

## 🐛 已知问题

- Cognee 是异步的，部分测试需要 `pytest-asyncio`
- Mem0 需要 API Key，未配置时自动降级

## 💡 编写新测试的建议

1. **使用 fixture**：避免重复初始化
2. **测试降级路径**：确保即使外部服务不可用也能工作
3. **使用临时目录**：避免污染真实数据
4. **Mock 外部调用**：LLM、API 调用都应该 Mock
5. **测试边界条件**：空输入、超长输入、特殊字符

---

*最后更新: 2026-06-15*
