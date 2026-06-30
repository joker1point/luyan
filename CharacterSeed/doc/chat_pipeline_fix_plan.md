# 对话管线修复 —— 详细实现计划

> 基于 2026-06-30 深度审计 | 修复覆盖：3 Critical + 6 High | 总改动量：~50 行代码 + 2 个配置变更

---

## 修复 1 (CRITICAL): 统一 `history_turns` 为 8

### 问题
`run()` 默认 10 轮，`run_stream()` 默认 5 轮。流式对话中 Director 看到的历史减半。

### 改动

**文件：** `backend/modules/interaction.py` L1445

```diff
-        history_turns: int = 5,  # [P1-3 修复] 10→5 缩短 history，降低 LLM 输入 token，TTFT 节省 300-800ms
+        history_turns: int = 8,  # [PIPE-1 修复] 与 run() 统一为 8，消除非对称行为
```

**理由：** 8 是折中选择（比 10 省 token，比 5 有更好的上下文一致性）。注释更新以反映统一决策。

**同时修改 L1071（run() 方法）：**
```diff
-        history_turns: int = 10,
+        history_turns: int = 8,  # [PIPE-1 修复] 统一为 8
```

### 验收
- `run()` 和 `run_stream()` 的历史轮数一致
- 流式对话的回复质量与同步模式一致

---

## 修复 2 (CRITICAL): Jiwen 竞争条件修复 — re-read 策略

### 问题
聊天线程在 post_chat 中 `apply_delta()` 时使用的是对话开始时（Node 1.5）的快照。如果调度器在对话期间 tick 了，apply_delta 会用旧值覆盖新值。

### 改动

**文件：** `backend/jiwen/jiwen_manager.py` L477-481 — `apply_delta()` 方法

```diff
def apply_delta(self, character_id: int, delta: Dict[str, float]) -> None:
    """聊天后调整状态"""
    engine = self.get_engine(character_id)
+   # [SYNC-1 修复] 在 apply 前 re-read 最新状态，避免覆盖调度器 tick 的更新
+   engine.load()
    engine.apply_delta(delta)
    engine.save()
```

**理由：** `engine.load()` 从 DB 重新拉取最新状态，确保 delta 作用于最新快照而非 N 分钟前的旧值。`load()` 通过 `on_load` callback 从 `jiwen_states` 表读取。

**确认：** `load()` 方法（`jiwen_core.py:191-212`）调用 `on_load()` callback 返回 DB 中的最新状态，不涉及额外 I/O（数据已在内存中）。

### 验收
- 通过 `POST /api/jiwen/{id}/tick` 手动推进状态
- 立即发送聊天消息
- 检查 `POST /api/jiwen/{id}/state` — 确认 delta 和 tick 的修改都被保留（不应互相覆盖）

---

## 修复 3 (CRITICAL): 流式管道记忆提取修复

### 问题
`run_stream()` 的持久化是异步的（`_persist_in_background`），post_chat_hooks 被调用时 `conversation_id=None`，导致 `post_chat.py:210` 的 `if conversation_id:` 守卫短路，跳过记忆提取。

### 改动

**方案 C（推荐）：** 在异步持久化完成后，用 callback 触发记忆提取。

**文件：** `backend/modules/post_chat.py` L209-221

```diff
# 2) 提取记忆
-    if extract_memories and conversation_id:
+    if extract_memories:
+        if not conversation_id:
+            # [POST-1 修复] conversation_id 为 None 时说明异步持久化未完成
+            # 等待一小段时间后重试获取 conversation_id
+            import time
+            for _ in range(3):  # 最多重试 3 次，每次 0.5s
+                time.sleep(0.5)
+                try:
+                    with sf() as rdb:
+                        # 通过 user_input + npc_response + timestamp 反查
+                        from datetime import datetime, timedelta, timezone
+                        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
+                        conv = rdb.query(Conversation).filter(
+                            Conversation.character_id == character_id,
+                            Conversation.user_input == user_input[:500],
+                            Conversation.timestamp >= recent,
+                        ).order_by(Conversation.timestamp.desc()).first()
+                        if conv:
+                            conversation_id = conv.id
+                            break
+                except Exception:
+                    pass
+        if conversation_id:
            try:
                with sf() as db:
                    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
                    if conv:
                        ids = memory_extractor.extract_and_save(
                            db=db, character_id=character_id, conversation=conv,
                        )
                        result["memories_extracted"] = ids
            except Exception as e:
                logger.warning("post_chat extract 失败: %s", e)
                result["errors"].append(f"extract: {e}")
```

**备选方案（更简单）：** 直接在 `run_stream()` 完成后传入 conversation_id

**文件：** `backend/modules/interaction.py` L1786-1797

```diff
# jiwen + 记忆/遗忘系统 后处理钩子（异步）
try:
+   # [POST-1 修复] 使用 bg_task_id 等待持久化完成
    from backend.modules.post_chat import post_chat_hooks
+   import threading, time
+   def _deferred_post_chat():
+       # 等待持久化线程完成
+       for _ in range(20):  # 最多等 2s
+           time.sleep(0.1)
+           try:
+               with GlobalSessionLocal() as check_db:
+                   conv = check_db.query(Conversation).filter(
+                       Conversation.character_id == character_id,
+                       Conversation.session_id == session_id,
+                   ).order_by(Conversation.id.desc()).first()
+                   if conv and conv.user_input == user_message:
+                       post_chat_hooks(
+                           conversation_id=conv.id,  # 现在有真实 ID
+                           ...)
+                   return
+           except Exception:
+               pass
+       # 超时：放弃记忆提取
+       post_chat_hooks(conversation_id=None, ...)
+   
    post_chat_hooks(
        character_id=character_id,
        user_input=user_message,
        npc_response=actor_speech,
        emotion_label=director_data.get("emotion"),
-       conversation_id=None,  # 异步持久化，id 不可用，post_chat 会跳过 extract
+       conversation_id=None,  # [POST-1] 由 _deferred_post_chat 处理
        run_in_background=True,
    )
except Exception as _e:
    logger.warning("post_chat_hooks dispatch 失败: %s", _e)
```

**推荐备选方案**：改动更集中（只在 interaction.py 中），不污染 post_chat.py 的简洁性。

### 验收
- 使用流式对话发送 3 条消息
- 检查 memories 表 — 应有新记录（非流式模式下已有的相同行为）
- 对比流式 vs 同步的 memories 提取数量 — 应接近

---

## 修复 4 (HIGH): 激活 EnhancedInteractionPipeline

### 问题
EnhancedInteractionPipeline 有完整的三层记忆系统（ContextManager + LongTermMemory + ShortTermMemory + KnowledgeBase），但 chat_router.py 从未调用。

### 改动

**文件：** `backend/state.py` L33-37

```diff
def get_pipeline():
    if "pipeline" not in _singletons:
        from backend.modules.interaction import InteractionPipeline
-       _singletons["pipeline"] = InteractionPipeline()
+       # [CTX-2 修复] 激活 EnhancedInteractionPipeline（三层记忆 + 语义检索）
+       base = InteractionPipeline()
+       try:
+           from backend.modules.enhanced_interaction import EnhancedInteractionPipeline
+           _singletons["pipeline"] = EnhancedInteractionPipeline(base_pipeline=base)
+       except Exception:
+           logger.warning("EnhancedInteractionPipeline 初始化失败，回退到基础管道")
+           _singletons["pipeline"] = base
    return _singletons["pipeline"]
```

**同时需要修复 CTX-1：** `context_manager.py:157` 的 `asyncio.run()` 问题

**文件：** `backend/memory/context_manager.py` L155-163

```diff
-        kb_results = asyncio.run(self.knowledge.search(query=query, k=3))
+        # [CTX-1 修复] 使用同步方式调用，避免 asyncio.run() 在事件循环中报错
+        try:
+            import asyncio
+            loop = asyncio.get_event_loop()
+            if loop.is_running():
+                # 在运行中的事件循环里，用 create_task 或直接调用同步方法
+                kb_results = self.knowledge.search_sync(query=query, k=3)
+            else:
+                kb_results = asyncio.run(self.knowledge.search(query=query, k=3))
+        except RuntimeError:
+            kb_results = self.knowledge.search_sync(query=query, k=3)
```

### 验收
- 对话发送后检查日志 — 应出现 ContextManager 的内存加载/检索日志
- 角色能够引用"很久以前说过但语义相关"的内容（而不仅仅是最近 5 条）
- 性能无显著退化（首次慢一些因为需要初始化 Mem0/知识库）

---

## 修复 5 (HIGH): ChatRequest.message 添加 max_length

### 改动

**文件：** `backend/schemas.py` L501

```diff
-    message: str
+    message: str = Field(..., max_length=5000, description="用户消息，最大 5000 字符")
```

**同时需要导入 Field：** 检查 L1-10 的 import，确认 `from pydantic import BaseModel, Field` 存在。

### 验收
- 发送 ≤5000 字符的消息 → 正常
- 发送 >5000 字符的消息 → 返回 422 Validation Error

---

## 修复 6 (HIGH): 流式端点错误处理

### 问题
`run_stream()` 中的错误通过 `yield ("error", ...)` 发出，但 HTTP 状态码已是 200。

### 改动

**文件：** `backend/api/chat_router.py` L76-112 — 在 `chat_stream` 函数开头加预检查

```diff
@router.post("/api/chat/stream")
async def chat_stream(request: ChatRequest, db: Session = Depends(get_db)):
+   # [C-4 修复] 预检查：在 yield 首个事件前验证角色存在
+   character = db.query(Character).filter(Character.id == request.character_id).first()
+   if not character:
+       raise HTTPException(status_code=404, detail="角色不存在")
+   
    pipeline = get_pipeline()
    def event_generator():
        try:
            for event_type, data in pipeline.run_stream(...):
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)[:200]})}\n\n"
    return StreamingResponse(...)
```

### 验收
- 请求不存在的角色 ID → HTTP 404（不再是 200 + error event）
- 正常流式对话 → 行为不变

---

## 修复 7 (HIGH): 回退短语多语言支持

### 问题
`FALLBACK_ACTOR_OUTPUT["speech"] = "（角色暂时无法回应）"` 硬编码中文。

### 改动

**文件：** `backend/modules/interaction.py` L270-274

```diff
FALLBACK_ACTOR_OUTPUT = {
-   "action": "站在原地，注视着玩家",
-   "expression": "...",
-   "speech": "（角色暂时无法回应）",
+   # [FB-1 修复] 回退值为 None，由调用方根据角色语言填充
+   "action": "...",
+   "expression": "neutral",
+   "speech": None,  # 由 actor 调用方填充
}
```

**同时修改 Actor.generate_with_fallback（L562-595）：**

```diff
    if parsed is None:
        parsed = dict(FALLBACK_ACTOR_OUTPUT)
+       if parsed.get("speech") is None:
+           # [FB-1 修复] 根据角色的 world_setting 或 config 选择回退语言
+           try:
+               char_data = self._get_char_data(character_name)
+               lang = _infer_language(char_data.get("world_setting", ""))
+               parsed["speech"] = _FALLBACK_PHRASES.get(lang, _FALLBACK_PHRASES["zh"])
+           except Exception:
+               parsed["speech"] = _FALLBACK_PHRASES["zh"]
    return parsed, raw
```

**新增语言映射表（同文件顶部）：**

```python
_FALLBACK_PHRASES = {
    "zh": "（角色暂时无法回应）",
    "en": "(The character is unable to respond right now)",
    "ja": "（キャラクターは今応答できません）",
}
```

### 验收
- 英文语境角色触发 LLM fallback → 回退消息为英文
- 中文语境角色 → 回退消息为中文

---

## 修复 8 (HIGH): 增强管道集成测试

### 问题
`test_enhanced_pipeline.py:166` 的 `test_run_basic_flow` Mock 了基础管道。

### 改动

**文件：** `tests/test_enhanced_pipeline.py` L151-196

```diff
def test_run_basic_flow(mock_pipeline, mock_creation_module, sample_character, db):
-   # [修复前] Mock基础管道
-   mock_base = MagicMock()
-   mock_base.run.return_value = {...}
-   pipeline = EnhancedInteractionPipeline(base_pipeline=mock_base)
-   result = pipeline.run(sample_character.id, "hello", db)
-   assert result == mock_base.run.return_value
+   # [TEST-4 修复] 使用真实基础管道（Mock LLM 调用）
+   from backend.modules.interaction import InteractionPipeline
+   base = InteractionPipeline()
+   # Mock LLMService 避免真实调用
+   from unittest.mock import patch
+   with patch.object(base.director, 'analyze_with_fallback', return_value=({...}, None)), \
+        patch.object(base.actor, 'generate_with_fallback', return_value=({...}, None)):
+       pipeline = EnhancedInteractionPipeline(base_pipeline=base)
+       result = pipeline.run(sample_character.id, "hello", db)
+       assert result is not None
+       assert "response" in result
```

### 验收
- 测试通过（不依赖真实 LLM）
- 增强管道和基础管道之间的接口兼容性得到验证

---

## 修复 9 (HIGH): `history_messages or None` 显式化

### 问题
依赖 Python 假值语义，如果将来代码改动可能静默降级为单轮模式。

### 改动

**文件：** `backend/modules/interaction.py` ~L1299-1308（Director 调用处）

```diff
director_data, director_raw = self.director.analyze_with_fallback(
    character_name=character.name,
    personality=personality,
    current_state=current_state,
    recent_memories=memories,
    user_input=user_message,
-   history_messages=history_messages or None,
+   history_messages=history_messages if history_messages else None,  # [EH-1] 显式检查
)
```

同样修改所有其他 `or None` 模式出现处（Actor 调用、run_stream 中的 Director/Actor 调用）。

### 验收
- 行为不变（仅是代码健壮性提升）

---

## 完整改动清单

| # | 严重度 | 问题ID | 文件 | 行 | 改动 | 行数 |
|:--:|:------:|--------|------|:--:|------|:--:|
| 1 | CRIT | PIPE-1 | `interaction.py` | 1445, 1071 | 统一 history_turns=8 | 2 |
| 2 | CRIT | SYNC-1 | `jiwen_manager.py` | 479 | apply_delta 前 re-read | +1 |
| 3 | CRIT | POST-1 | `interaction.py` | 1786-1797 | 异步持久化后 deferred post_chat | ~20 |
| 4 | HIGH | CTX-2 | `state.py` | 33-37 | 激活 EnhancedInteractionPipeline | ~8 |
| 5 | HIGH | CTX-1 | `context_manager.py` | 155-163 | asyncio.run → 同步调用 | ~8 |
| 6 | HIGH | C-1 | `schemas.py` | 501 | message 加 max_length=5000 | 1 |
| 7 | HIGH | C-4 | `chat_router.py` | 78 | 流式端点预检查 | +5 |
| 8 | HIGH | FB-1 | `interaction.py` | 270-274 | 回退短语多语言 | ~20 |
| 9 | HIGH | TEST-4 | `test_enhanced_pipeline.py` | 166 | 取消 Mock | ~10 |
| 10 | HIGH | EH-1 | `interaction.py` | 1299+ | `or None` → 显式 if-else | ~5 |

**总计：10 个修复，~80 行代码改动，无新建文件。**

---

## 执行顺序

```
Phase 1（20min）— 低风险单行修复
  1. C-1：schemas.py max_length
  2. PIPE-1：history_turns 统一
  3. EH-1：or None 显式化

Phase 2（30min）— 中风险逻辑修复
  4. SYNC-1：jiwen_manager apply_delta re-read
  5. C-4：流式预检查
  6. FB-1：回退多语言

Phase 3（60min）— 高风险架构修复
  7. POST-1：流式记忆提取
  8. CTX-1 + CTX-2：激活增强管道

Phase 4（30min）— 测试验证
  9. TEST-4：修复集成测试
  10. 运行全部现有测试 + E2E 烟测
```

---

## 回滚策略

每个修复都是独立的、可单独回滚的。如果某个修复引入问题：

```bash
# 使用 harness 日志系统定位出问题的修复
cd .workbuddy/harness
python change_logger.py history --limit 10

# 回滚到出问题之前的 commit
python change_logger.py rollback --to <hash> --dry-run
python change_logger.py rollback --to <hash>
```
