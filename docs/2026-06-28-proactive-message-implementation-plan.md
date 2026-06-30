# 主动消息与对话系统集成 - 实现计划

**日期**: 2026-06-28  
**项目**: CharacterSeed  
**方法**: grill-me 深度拷问后形成

---

## 一、当前进度总览

### ✅ 已完成（P1-P4）

| # | 任务 | 状态 | 文件 |
|---|------|------|------|
| P1 | Conversation 新增 `is_proactive` 字段 | ✅ 完成 | `backend/models.py`, `backend/services/db_migration.py` |
| P2 | `consume_and_insert()` 方法 | ✅ 完成 | `backend/jiwen/jiwen_manager.py` |
| P3 | SSE 推送端点 `/api/jiwen/proactive/stream` | ✅ 完成 | `backend/api/jiwen_router.py` |
| P4 | 前端全局 SSE 订阅 `useProactive` hook | ✅ 完成 | `web/react-vite/src/hooks/useProactive.js` |

### 🚧 待完成（P5-P7）

| # | 任务 | 状态 | 依赖 |
|---|------|------|------|
| P5 | 通知栏组件 + ChatPage 感知 | ⏳ 待实现 | P4 |
| P6 | LLM 动态生成 + fallback | ⏳ 待实现 | P2 |
| P7 | `reset_connection` 集成 | ⏳ 待实现 | P5 |
| V1 | 构建验证 + 端到端测试 | ⏳ 待实现 | P1-P7 |

---

## 二、剩余任务详细计划

### P5: 通知栏组件 + ChatPage 感知

**目标**: 用户能在前端看到主动消息通知，并在 ChatPage 中直接看到消息。

#### 5.1 通知栏组件

**文件**: `web/react-vite/src/components/ProactiveNotificationBar.jsx`（新建）

**功能**:
- 固定在页面顶部（NavBar 下方）
- 显示角色名 + 消息预览（最多 5 条）
- 点击通知 → 调用 `consumeMessage()` → 跳转到对应 ChatPage
- 支持关闭单条通知

**实现要点**:
```jsx
import { useProactive } from '../hooks/useProactive'
import { useNavigate } from 'react-router-dom'

export default function ProactiveNotificationBar() {
  const { messages, consumeMessage, clearMessage } = useProactive()
  const navigate = useNavigate()

  const handleClick = async (msg) => {
    await consumeMessage(msg.id)
    navigate(`/chat?characterId=${msg.character_id}&sessionId=${msg.session_id}`)
  }

  if (messages.length === 0) return null

  return (
    <div className="proactive-notification-bar">
      {messages.slice(0, 5).map(msg => (
        <div key={msg.id} className="notification-item" onClick={() => handleClick(msg)}>
          <span className="notification-content">{msg.content}</span>
          <button onClick={(e) => { e.stopPropagation(); clearMessage(msg.id) }}>×</button>
        </div>
      ))}
    </div>
  )
}
```

**集成位置**: `App.jsx` 中 `<CharactersProvider>` 之后、`<Outlet />` 之前

#### 5.2 ChatPage 感知

**文件**: `web/react-vite/src/pages/ChatPage.jsx`（修改）

**功能**:
- 监听 `useProactive` 的 `messages`
- 匹配当前 `characterId` → 直接插入消息列表底部
- 不匹配 → 不处理（交给通知栏）

**实现要点**:
```jsx
import { useProactive } from '../hooks/useProactive'

export default function ChatPage() {
  const { messages } = useProactive()
  const characterId = useSearchParams().get('characterId')

  // 过滤出当前角色的主动消息
  const proactiveMessages = messages.filter(m => String(m.character_id) === characterId)

  // 合并到消息列表
  const allMessages = useMemo(() => {
    const existing = conversations.map(c => ({
      id: c.id,
      role: 'assistant',
      content: c.npc_response,
      is_proactive: c.is_proactive,
      timestamp: c.timestamp,
    }))
    
    // 追加未消费的主动消息（预览）
    const proactive = proactiveMessages.map(m => ({
      id: `proactive-${m.id}`,
      role: 'assistant',
      content: m.content,
      is_proactive: true,
      timestamp: m.timestamp,
      pending: true, // 标记为待消费
    }))
    
    return [...existing, ...proactive].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
  }, [conversations, proactiveMessages])

  // ... 渲染 allMessages
}
```

**验收标准**:
- ✅ 触发主动消息 → 通知栏弹出
- ✅ 点击通知 → 跳转到 ChatPage → 看到消息
- ✅ 在 ChatPage 时 → 消息直接出现在列表底部

---

### P6: LLM 动态生成 + fallback

**目标**: 主动消息内容由 LLM 根据角色状态动态生成，失败时 fallback 到模板。

#### 6.1 动态生成模块

**文件**: `backend/modules/proactive.py`（新建）

**功能**:
- 接收 `character_id` + `trigger_state`
- 调用 LLM 生成符合角色性格和情绪的消息
- 失败时返回硬编码模板

**实现要点**:
```python
from backend.services.llm_service import LLMService
from backend.jiwen import get_jiwen_manager
from backend.models import Character

async def generate_proactive_content(character_id: int, trigger_state: Dict) -> str:
    """LLM 动态生成主动消息内容"""
    try:
        character = db.query(Character).filter(Character.id == character_id).first()
        if not character:
            return get_fallback_template(trigger_state)
        
        jiwen_state = get_jiwen_manager().get_state(character_id)
        
        prompt = f"""角色：{character.name}
灵魂设定：{character.soul_md or character.description}
当前情绪状态：
- connection: {jiwen_state.get('connection', 0):.2f}
- pride: {jiwen_state.get('pride', 0):.2f}
- valence: {jiwen_state.get('valence', 0):.2f}
- arousal: {jiwen_state.get('arousal', 0):.2f}
触发原因：{trigger_state.get('reason', 'contact')}

请生成一句角色会主动说的话（1-2句，符合角色性格和当前情绪，自然流畅）："""
        
        llm = LLMService()
        content = await llm.call(prompt, task='proactive')
        return content.strip()
    except Exception as e:
        logger.warning(f"LLM 生成主动消息失败: {e}")
        return get_fallback_template(trigger_state)

def get_fallback_template(trigger_state: Dict) -> str:
    """硬编码 fallback 模板"""
    connection = trigger_state.get('connection', 0)
    pride = trigger_state.get('pride', 0)
    
    if connection > 0.7:
        return "突然想起你了，最近过得怎么样？"
    elif connection > 0.4:
        return "好久不见，一切都好吗？"
    elif pride > 0.6:
        return "今天心情不错，想和你聊聊。"
    else:
        return "在忙什么呢？"
```

#### 6.2 集成到 jiwen_manager

**文件**: `backend/jiwen/jiwen_manager.py`（修改）

**修改** `_handle_contact_triggers()`:
```python
async def _handle_contact_triggers(self, character_id: int, trigger_state: Dict):
    """处理 contact 触发器 → 生成主动消息"""
    # 异步生成消息内容
    asyncio.create_task(
        self._generate_and_store_proactive_message(character_id, trigger_state)
    )

async def _generate_and_store_proactive_message(self, character_id: int, trigger_state: Dict):
    """生成并存储主动消息"""
    try:
        from backend.modules.proactive import generate_proactive_content
        content = await generate_proactive_content(character_id, trigger_state)
        
        with self._db() as db:
            msg = ProactiveMessage(
                character_id=character_id,
                content=content,
                trigger_id=trigger_state.get('trigger_id'),
                consumed=0,
            )
            db.add(msg)
            db.commit()
            
            # 推送到 SSE
            from backend.api.jiwen_router import push_proactive_message
            await push_proactive_message({
                'message_id': msg.id,
                'character_id': character_id,
                'content': content,
            })
    except Exception as e:
        logger.error(f"生成主动消息失败: {e}")
```

**验收标准**:
- ✅ 触发 contact → 检查 `proactive_messages` 内容是 LLM 生成的
- ✅ LLM 调用失败 → fallback 到硬编码模板
- ✅ 消息推送到 SSE 客户端

---

### P7: reset_connection 集成

**目标**: 用户回复主动消息后，重置 connection 并计算情绪 delta。

#### 7.1 修改 post_chat_hooks

**文件**: `backend/api/chat_router.py` 或 `backend/modules/post_chat.py`

**实现要点**:
```python
def _run_hooks_sync(
    character_id: int,
    user_input: str,
    npc_response: str,
    emotion_label: Optional[str] = None,
    is_reply_to_proactive: bool = False,
):
    """聊天后钩子函数"""
    mgr = get_jiwen_manager()
    
    # 1) 如果是回复主动消息 → reset connection
    if is_reply_to_proactive:
        mgr.reset_connection(character_id)
        logger.info(f"reset_connection 已调用 (character_id={character_id})")
    
    # 2) 计算情绪 delta
    delta = infer_emotion_delta(user_input, npc_response, emotion_label)
    if delta:
        mgr.apply_delta(character_id, delta)
        logger.info(f"apply_delta 已调用: {delta}")
```

#### 7.2 判断是否为回复主动消息

**修改** `chat_router.py`:
```python
@router.post("/{character_id}/chat")
async def chat(character_id: int, req: ChatRequest, db: Session = Depends(get_db)):
    # ... 现有逻辑 ...
    
    # 判断上一条是否是主动消息
    last_conv = db.query(Conversation).filter(
        Conversation.character_id == character_id,
        Conversation.session_id == req.session_id,
    ).order_by(Conversation.timestamp.desc()).first()
    
    is_reply_to_proactive = last_conv and last_conv.is_proactive
    
    # 调用 hooks
    _run_hooks_sync(
        character_id=character_id,
        user_input=req.user_input,
        npc_response=response['content'],
        emotion_label=response.get('emotion'),
        is_reply_to_proactive=is_reply_to_proactive,
    )
```

**验收标准**:
- ✅ 回复主动消息后 → 检查 Jiwen connection 归零
- ✅ 情绪 delta 正确计算并应用

---

## 三、构建验证 + 端到端测试

### V1: 构建验证

**步骤**:
1. 前端构建：`npm run build`
2. 检查构建产物：`dist/assets/` 下应有 `ProactiveNotificationBar` chunk
3. 后端启动：`python -m uvicorn backend.main:app`
4. 检查数据库迁移：`conversations` 表应有 `is_proactive` 列

### V2: 端到端测试

**测试场景**:
1. **触发主动消息**:
   - 启动调度器：`POST /api/jiwen/scheduler/start`
   - 等待 tick 触发 contact
   - 检查 `proactive_messages` 表有新记录
   - 检查 SSE 客户端收到事件

2. **前端通知栏**:
   - 打开浏览器 → 看到通知栏弹出
   - 点击通知 → 跳转到 ChatPage
   - 看到主动消息在列表底部

3. **用户回复**:
   - 在 ChatPage 输入消息
   - 检查 `conversations` 表新记录 `is_proactive=False`
   - 检查 Jiwen connection 归零

4. **LLM 生成**:
   - 触发 contact → 检查消息内容是 LLM 生成的（非模板）
   - 模拟 LLM 失败 → 检查 fallback 到模板

---

## 四、风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| SSE 连接泄漏 | 30s heartbeat + 断连自动清理（已实现） |
| 主动消息堆积 | 前端限制最多显示 5 条通知 |
| LLM 调用超时 | `asyncio.wait_for(timeout=10)` + fallback |
| 数据库迁移失败 | 幂等迁移（检查列是否存在） |
| 多 tab 重复消费 | 前端 localStorage 记录已消费 ID（待实现） |

---

## 五、待确认项

1. **分支 11 过期策略**: 24h 未消费自动标记过期，还是永久保留？
   - **推荐**: 24h 过期（避免消息堆积）
   - **实现**: 定时任务扫描 `proactive_messages` 表

2. **P6 LLM 调用**: 使用现有 LLMService 还是新建专用实例？
   - **推荐**: 使用现有 LLMService（单例模式，节省资源）

3. **通知栏 UI**: 是否需要声音/震动提醒？
   - **推荐**: 第一版不做（避免打扰用户），后续可加

---

## 六、实施顺序

**建议顺序**（按依赖关系）:
1. **P5**: 通知栏 + ChatPage 感知（前端可见）
2. **P7**: reset_connection 集成（状态反馈）
3. **P6**: LLM 动态生成（内容优化）
4. **V1-V2**: 构建验证 + 端到端测试

**预计工作量**:
- P5: 2-3 小时
- P6: 1-2 小时
- P7: 1 小时
- V1-V2: 1-2 小时
- **总计**: 5-8 小时

---

## 七、验收标准汇总

| # | 验收项 | 预期结果 |
|---|--------|----------|
| 1 | 角色 tick 触发 contact | 前端通知栏弹出 |
| 2 | 用户点击通知 | 跳转到对应 session，看到主动消息 |
| 3 | 主动消息在 ChatPage 显示 | 消息列表底部出现，带 `is_proactive` 标记 |
| 4 | 用户回复主动消息 | connection 归零 + 情绪 delta 计算 |
| 5 | LLM 上下文区分 | prompt 中标记 `[角色主动发起]` |
| 6 | 24h 未消费 | 自动标记过期，不写入 conversations（待实现） |
| 7 | LLM 生成失败 | fallback 到硬编码模板 |
| 8 | SSE 断连重连 | 前端自动重连，不丢消息 |

---

## 八、不做的事（明确排除）

- ❌ 不新增 `user_id` 字段（当前单用户应用）
- ❌ 不用 WebSocket（SSE 足够，项目已有 SSE 基础设施）
- ❌ 不在 tick 时同步调用 LLM（会阻塞调度器）
- ❌ 不在 `user_input` 里加 `[主动消息]` 前缀（用 `is_proactive` 字段）
- ❌ 不做声音/震动提醒（第一版避免打扰用户）

---

**下一步**: 确认本计划后，按 P5 → P7 → P6 → V1-V2 顺序实施。
