# 主动消息与对话系统集成计划

**日期**: 2026-06-28  
**项目**: CharacterSeed  
**方法**: grill-me 深度拷问后形成

---

## 一、决策总结（grill-me 10+ 分支共识）

| # | 决策 | 方案 |
|---|------|------|
| 1 | 主动消息进入对话流 | 消费后写入 conversations 表（role=assistant） |
| 2 | 推送机制 | SSE 替代轮询 |
| 3 | 用户回复后的状态反馈 | reset_connection() + infer_emotion_delta() |
| 4 | 消息内容生成 | 模板 + LLM 动态生成混合（asyncio.create_task + fallback） |
| 5 | LLM 上下文融合 | is_proactive 标记，LLM prompt 中区分主动/被动 |
| 6 | 消费事务 | consume_and_insert() 同一 DB 事务 |
| 7 | 前端感知 | 全局 SSE + ChatPage 感知 + 通知栏三层架构 |
| 8 | session 管理 | lazy session 绑定（consume 时 find_or_create_session） |
| 9 | LLM 触发时机 | asyncio.create_task() + fallback 模板 |
| 10 | is_proactive 实现 | 新增 Boolean 字段 + 数据库迁移 |
| 11 | 过期策略 | 24h 未消费自动标记过期（推荐，待确认） |

---

## 二、数据流全景

```
Jiwen tick → contact 触发器
    ↓
asyncio.create_task(llm_generate) → 成功/失败 fallback
    ↓
写入 proactive_messages（不绑定 session）
    ↓
SSE /api/proactive/stream 推送
    ↓
┌─── 用户在 ChatPage 且是同一角色？
│     └── 是 → 直接插入消息列表底部
│     └── 否 → 通知栏弹出
│
└─── 用户点击通知 / 手动查看
      ↓
POST /api/proactive/consume/{id}
      ↓
consume_and_insert() [同一事务]:
  1. find_or_create_session() → session_id
  2. 标记 proactive_messages.consumed = 1
  3. 插入 conversations(is_proactive=True)
      ↓
返回 session_id → 前端跳转
```

---

## 三、实现步骤（按依赖顺序）

### P1: Conversation 新增 is_proactive 字段

**文件**: `backend/models.py`, `backend/services/db_migration.py`

```python
# models.py - Conversation 类新增
is_proactive = Column(Boolean, nullable=False, default=False, index=True)
```

```python
# db_migration.py 新增迁移函数
def migrate_add_is_proactive():
    """v007: Conversation 新增 is_proactive 字段"""
    # ALTER TABLE conversations ADD COLUMN is_proactive BOOLEAN NOT NULL DEFAULT 0
    # 幂等：检查列是否已存在
```

**验证**: 启动后端，检查 conversations 表有 is_proactive 列

---

### P2: consume_and_insert() 方法

**文件**: `backend/jiwen/jiwen_manager.py`

```python
def consume_and_insert(self, message_id: int) -> Dict[str, Any]:
    """消费主动消息 + 写入 conversations（同一事务）
    
    Returns:
        {"session_id": int, "conversation_id": int, "character_id": int}
    """
    with self._db() as db:
        # 1. 获取主动消息
        msg = db.query(ProactiveMessage).filter(
            ProactiveMessage.id == message_id,
            ProactiveMessage.consumed == 0,
        ).first()
        if not msg:
            return None
        
        # 2. find_or_create_session
        session = _find_or_create_session(db, msg.character_id)
        
        # 3. 标记 consumed
        msg.consumed = 1
        
        # 4. 写入 conversations
        conv = Conversation(
            character_id=msg.character_id,
            session_id=session.id,
            user_input="",  # 主动消息无用户输入
            npc_response=msg.content,
            is_proactive=True,
        )
        db.add(conv)
        db.commit()
        
        return {
            "session_id": session.id,
            "conversation_id": conv.id,
            "character_id": msg.character_id,
        }
```

```python
def _find_or_create_session(db, character_id: int) -> ChatSession:
    """复用最近 24h 内的 session；超过 24h 则新建"""
    recent = db.query(ChatSession).filter(
        ChatSession.character_id == character_id,
        ChatSession.updated_at >= datetime.utcnow() - timedelta(hours=24),
    ).order_by(ChatSession.updated_at.desc()).first()
    
    if recent:
        return recent
    
    session = ChatSession(
        character_id=character_id,
        title=f"主动消息 {datetime.utcnow().strftime('%m-%d %H:%M')}",
    )
    db.add(session)
    db.flush()
    return session
```

**验证**: 单元测试 — 消费后检查 conversations 表有 is_proactive=True 的记录

---

### P3: SSE 推送端点

**文件**: `backend/api/jiwen_router.py`（或新建 `backend/api/proactive_router.py`）

```python
# 全局 SSE 连接管理
_sse_clients: Dict[int, asyncio.Queue] = {}  # client_id → queue

@router.get("/api/proactive/stream")
async def proactive_stream(request: Request):
    """全局 SSE 长连接，推送所有主动消息"""
    client_id = id(request)
    queue = asyncio.Queue()
    _sse_clients[client_id] = queue
    
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"event: proactive_message\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield f": heartbeat\n\n"  # keepalive
        finally:
            _sse_clients.pop(client_id, None)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# 推送函数（供 consume_and_insert 调用）
async def push_proactive_message(message: Dict[str, Any]):
    for queue in _sse_clients.values():
        await queue.put(message)
```

**验证**: curl 连接 SSE 端点，手动触发 tick，确认收到事件

---

### P4: 前端全局 SSE 订阅

**文件**: `web/react-vite/src/hooks/useProactive.js`（新建）

```javascript
import { useEffect, useRef, useCallback } from 'react'

let globalQueue = []
let listeners = new Set()
let es = null

function connect() {
  if (es) return
  es = new EventSource('/api/proactive/stream')
  es.addEventListener('proactive_message', (e) => {
    const msg = JSON.parse(e.data)
    globalQueue.push(msg)
    listeners.forEach(fn => fn(msg))
  })
  es.onerror = () => { es.close(); es = null; setTimeout(connect, 3000) }
}

export function useProactive() {
  const [messages, setMessages] = useState([])
  
  useEffect(() => {
    connect()
    const handler = (msg) => setMessages(prev => [...prev, msg])
    listeners.add(handler)
    return () => listeners.delete(handler)
  }, [])
  
  return { messages }
}
```

**验证**: 在 App.jsx 中引入 useProactive，确认收到事件

---

### P5: 通知栏组件 + ChatPage 感知

**文件**: 
- `web/react-vite/src/components/NotificationBar.jsx`（新建）
- `web/react-vite/src/pages/ChatPage.jsx`（修改）

**通知栏**:
- 固定在页面顶部
- 显示角色名 + 消息预览
- 点击 → 调用 consume API → 跳转到对应 session

**ChatPage 感知**:
- watch useProactive 的 messages
- 匹配当前 characterId → 直接插入消息列表底部
- 不匹配 → 不处理（交给通知栏）

**验证**: 触发主动消息 → 通知栏弹出 → 点击跳转 → 看到消息

---

### P6: LLM 动态生成 + fallback

**文件**: `backend/modules/proactive.py`（新建）

```python
async def generate_proactive_content(character_id: int, trigger_state: Dict) -> str:
    """LLM 动态生成主动消息内容"""
    try:
        # 获取角色设定 + Jiwen 状态
        character = get_character(character_id)
        jiwen_state = get_jiwen_manager().get_state(character_id)
        
        prompt = f"""角色：{character.name}
灵魂设定：{character.soul_md}
当前情绪：{jiwen_state}
触发原因：{trigger_state.get('reason', '')}

请生成一句角色会主动说的话（1-2句，符合角色性格和当前情绪）："""
        
        content = await llm_service.call(prompt)
        return content.strip()
    except Exception:
        return get_fallback_template(trigger_state)

def get_fallback_template(trigger_state: Dict) -> str:
    """硬编码 fallback 模板"""
    connection = trigger_state.get('connection', 0)
    pride = trigger_state.get('pride', 0)
    # ... 和现有 _handle_contact_triggers 逻辑一致
```

**修改** `jiwen_manager.py` 的 `_handle_contact_triggers()`:
```python
# 替换硬编码为 asyncio.create_task
asyncio.create_task(
    generate_and_store_proactive_message(character_id, trigger_state)
)
```

**验证**: 触发 contact → 检查 proactive_messages 内容是 LLM 生成的（非模板）

---

### P7: reset_connection 集成

**文件**: `backend/api/chat_router.py` 或 `backend/modules/post_chat.py`

当用户回复一条 is_proactive=True 的对话时：
1. 调用 `reset_connection()` — connection 归零
2. 调用 `infer_emotion_delta()` — 计算情绪 delta
3. 调用 `apply_delta()` — 应用情绪变化

**修改** `post_chat_hooks()`:
```python
def _run_hooks_sync(..., is_reply_to_proactive: bool = False):
    # 1) jiwen applyDelta
    if is_reply_to_proactive:
        get_jiwen_manager().reset_connection(character_id)
    
    delta = infer_emotion_delta(user_input, npc_response, emotion_label)
    if delta:
        get_jiwen_manager().apply_delta(character_id, delta)
```

**验证**: 回复主动消息后，检查 Jiwen connection 归零

---

## 四、验收标准

| # | 验收项 | 预期结果 |
|---|--------|----------|
| 1 | 角色 tick 触发 contact | 前端通知栏弹出 |
| 2 | 用户点击通知 | 跳转到对应 session，看到主动消息 |
| 3 | 主动消息在 ChatPage 显示 | 消息列表底部出现，带 is_proactive 标记 |
| 4 | 用户回复主动消息 | connection 归零 + 情绪 delta 计算 |
| 5 | LLM 上下文区分 | prompt 中标记 [角色主动发起] |
| 6 | 24h 未消费 | 自动标记过期，不写入 conversations |
| 7 | LLM 生成失败 | fallback 到硬编码模板 |
| 8 | SSE 断连重连 | 前端自动重连，不丢消息 |

---

## 五、风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| SSE 连接泄漏 | 30s heartbeat + 断连自动清理 |
| 主动消息堆积 | 24h 过期策略 + 前端限制最多显示 5 条通知 |
| LLM 调用超时 | asyncio.wait_for(timeout=10) + fallback |
| 数据库迁移失败 | 幂等迁移（检查列是否存在） |
| 多 tab 重复消费 | 前端 localStorage 记录已消费 ID |

---

## 六、不做的事（明确排除）

- ❌ 不新增 user_id 字段（当前单用户应用）
- ❌ 不用 WebSocket（SSE 足够，项目已有 SSE 基础设施）
- ❌ 不在 tick 时同步调用 LLM（会阻塞调度器）
- ❌ 不在 user_input 里加 "[主动消息]" 前缀（用 is_proactive 字段）

---

## 七、待确认项

1. **分支 11 过期策略**: 24h 未消费自动标记过期，还是永久保留？
2. **P6 LLM 调用**: 使用现有 LLMService 还是新建专用实例？
3. **通知栏 UI**: 是否需要声音/震动提醒？
