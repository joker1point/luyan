# Jiwen 情绪引擎集成经验总结

**日期**: 2026-06-28  
**项目**: CharacterSeed  
**集成内容**: Jiwen 情绪引擎完整集成（后端 + 前端）

---

## 一、项目背景

Jiwen 是一个情绪引擎，管理角色的五轴连续情绪状态（connection/pride/valence/arousal/immersion），支持数学漂移、阈值触发器、LLM prompt 注入等功能。本次集成目标是将 Jiwen 引擎完整接入 CharacterSeed 系统，实现从后端状态管理到前端展示的完整链路。

---

## 二、完成的工作

### 2.1 后端集成（已完成 10/13）

#### ✅ 数据模型层
- **文件**: `backend/models.py`
- **内容**: 
  - `JiwenState` 表：五轴连续状态 + 用户状态/活动类型
  - `JiwenTrigger` 表：触发器记录（action/reason/state_json/consumed）
  - `ProactiveMessage` 表：主动消息队列（本次新增）

#### ✅ 数据库迁移
- **文件**: `backend/services/db_migration.py`
- **内容**: `run_all_migrations()` 在启动时自动执行

#### ✅ API 路由
- **文件**: `backend/api/jiwen_router.py`
- **端点**: 17 个 REST 端点
  - 状态查询/更新
  - 触发器管理
  - 调度器控制（启动/停止/状态）
  - 记忆统计
  - **主动消息查询和消费**（本次新增）

#### ✅ 后台调度器
- **文件**: `backend/main.py`
- **配置**: `start_scheduler(interval_seconds=300)` 每 5 分钟 tick 一次

#### ✅ LLM Prompt 注入
- **文件**: `backend/modules/interaction.py`
- **节点 1.5**: Director prompt 注入 jiwen 五轴状态
- **节点 4**: Actor style guidance 注入情绪风格指引

#### ✅ 聊天后情绪更新
- **文件**: `backend/modules/post_chat.py`
- **逻辑**: `post_chat_hooks()` 调用 `infer_emotion_delta()` + `apply_delta()`

#### ✅ 触发器消费机制（本次重点）
- **文件**: `backend/jiwen/jiwen_manager.py`
- **实现**: `_handle_contact_triggers()` 方法
  - 检测 contact 触发器
  - 根据情绪状态生成不同的主动消息内容
  - 写入 `proactive_messages` 表

### 2.2 前端集成（已完成）

#### ✅ API 封装
- **文件**: `web/react-vite/src/utils/realApi.js`
- **新增方法**:
  - `getJiwenState(characterId)`
  - `getJiwenTriggers(characterId, limit)`
  - `tickJiwen(characterId, ticks)`
  - `getJiwenSchedulerStatus()`
  - `startJiwenScheduler(intervalSeconds)`
  - `stopJiwenScheduler()`
  - `tickAllJiwen()`
  - `getProactiveMessages(characterId, limit, unconsumedOnly)`
  - `consumeProactiveMessage(characterId, messageId)`

#### ✅ 状态展示组件
- **文件**: `web/react-vite/src/components/JiwenStatePanel.jsx`
- **功能**:
  - 五轴状态可视化（进度条）
  - 情绪状态描述（悠闲/在想念/想开口/坐不住等）
  - 最近触发器列表
  - 手动 tick 按钮
  - 30 秒自动刷新

#### ✅ 控制面板页面
- **文件**: `web/react-vite/src/pages/JiwenPage.jsx`
- **功能**:
  - 角色列表展示
  - 调度器控制（启动/停止/状态显示）
  - 立即 tick 全部角色
  - 主动消息队列展示
  - 消息标记已读功能

#### ✅ 路由配置
- **文件**: `web/react-vite/src/router/routes.js`
- **路径**: `/jiwen` → JiwenPage
- **文件**: `web/react-vite/src/router/lazyPages.js`
- **懒加载**: `jiwen: lazy(() => import('../pages/JiwenPage'))`

---

## 三、踩坑与解决方案

### 3.1 ProactiveMessage 模型未定义

**问题**: 
- 初始清单中 `proactive_messages` 表只在注释中提到，没有实际定义
- contact 触发器只是写入 `jiwen_triggers` 表，没有入队机制

**解决方案**:
```python
# backend/models.py
class ProactiveMessage(Base):
    __tablename__ = "proactive_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    trigger_id = Column(Integer, ForeignKey("jiwen_triggers.id"), nullable=True)
    consumed = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
```

### 3.2 Contact 触发器没有消费逻辑

**问题**:
- `tick_character()` 会 persist_triggers，但 contact 触发器没有后续处理
- 前端无法接收主动消息

**解决方案**:
```python
# backend/jiwen/jiwen_manager.py
def _handle_contact_triggers(self, character_id, triggers, trigger_ids):
    """处理 contact 触发器：生成主动消息并入库"""
    with self._db() as db:
        for i, t in enumerate(triggers):
            if t.get("action") == "contact" and i < len(trigger_ids):
                # 根据情绪状态生成不同的开场白
                state = t.get("state_at_trigger", {})
                connection = state.get("connection", 0)
                pride = state.get("pride", 0)
                
                if connection >= 0.5:
                    content = "在忙吗？想找你聊聊。" if pride < 0.3 else "（嘴硬地）人呢？怎么不说话了？"
                elif connection >= 0.35:
                    content = "最近怎么样？" if pride < 0.3 else "（犹豫了一下）...在吗？"
                else:
                    content = "嘿，有空吗？"
                
                msg = ProactiveMessage(
                    character_id=character_id,
                    content=content,
                    trigger_id=trigger_ids[i],
                )
                db.add(msg)
        db.commit()
```

### 3.3 前端缺少 Jiwen API 方法

**问题**:
- `realApi.js` 中没有 Jiwen 相关的 API 封装
- 前端组件无法调用后端接口

**解决方案**:
在 `realApi.js` 中添加完整的 Jiwen API 方法封装，包括状态查询、触发器管理、调度器控制、主动消息等。

### 3.4 前端路由未配置

**问题**:
- JiwenPage 创建后无法访问，因为没有配置路由

**解决方案**:
1. 在 `routes.js` 中添加 `{ path: '/jiwen', title: '积温', pageKey: 'jiwen', showInNav: true }`
2. 在 `lazyPages.js` 中添加 `jiwen: lazy(() => import('../pages/JiwenPage'))`

---

## 四、技术要点

### 4.1 触发器消费机制设计

**核心思路**:
1. Jiwen 引擎 tick 时检测阈值，生成触发器
2. `tick_character()` 调用 `_handle_contact_triggers()` 处理 contact 类型触发器
3. 根据当前情绪状态（connection/pride）生成不同的主动消息内容
4. 消息写入 `proactive_messages` 表，等待前端消费
5. 前端通过轮询（30 秒间隔）获取未消费消息
6. 用户查看后调用 `/consume` 接口标记为已读

**情绪状态 → 消息内容映射**:
- 高 connection + 低 pride → 温和开场："在忙吗？想找你聊聊。"
- 高 connection + 高 pride → 傲娇开场："（嘴硬地）人呢？怎么不说话了？"
- 中 connection + 低 pride → 普通问候："最近怎么样？"
- 中 connection + 高 pride → 犹豫开场："（犹豫了一下）...在吗？"
- 低 connection → 直接开场："嘿，有空吗？"

### 4.2 前端状态管理

**组件结构**:
```
JiwenPage (控制面板)
├── 调度器控制（启动/停止/tick全部）
├── 角色列表（左侧）
└── 角色详情（右侧）
    ├── JiwenStatePanel (五轴状态 + 触发器)
    └── ProactiveMessagesPanel (主动消息队列)
```

**状态刷新策略**:
- JiwenStatePanel: 30 秒自动刷新
- ProactiveMessagesPanel: 30 秒自动刷新
- 手动操作后立即刷新（tick/consume）

### 4.3 数据库表设计

**关键索引**:
- `proactive_messages.character_id`: 按角色查询消息
- `proactive_messages.created_at`: 按时间排序
- `proactive_messages.consumed`: 过滤未消费消息

**外键关系**:
- `character_id` → `characters.id` (CASCADE DELETE)
- `trigger_id` → `jiwen_triggers.id` (可选，用于追溯触发源)

---

## 五、未完成项（优先级 3）

### 5.1 世界引擎集成

**现状**: 
- `world_engine.py` 返回世界上下文（季节/天气/事件）
- 世界变化不会触发 jiwen 情绪更新

**建议方案**:
```python
# backend/world/world_engine.py
def apply_world_event(self, character_id, event):
    """世界事件影响角色情绪"""
    if event.type == "weather_change" and event.weather == "rainy":
        # 下雨天可能让角色低落
        delta = {"valence": -0.1, "arousal": -0.05}
        jiwen_manager.apply_delta(character_id, delta)
```

### 5.2 记忆模块集成

**现状**:
- `memory/short_term.py`, `long_term.py`, `context_manager.py` 没有使用 jiwen 状态

**建议方案**:
```python
# backend/memory/context_manager.py
def retrieve_memories(self, character_id, query):
    """情绪一致性记忆检索"""
    jiwen_state = jiwen_manager.get_state(character_id)
    # 高 arousal 时优先检索情绪强烈的记忆
    if jiwen_state.arousal > 0.3:
        memories = self.long_term.get_high_arousal_memories(character_id)
    else:
        memories = self.long_term.get_recent_memories(character_id)
    return memories
```

### 5.3 记忆提取器增强

**现状**:
- `memory_extractor.py` 提取记忆碎片并保存
- 没有从 jiwen 状态中提取情绪相关记忆的特殊逻辑

**建议方案**:
```python
# backend/memory/memory_extractor.py
def extract_emotional_memories(self, character_id, conversation):
    """高 arousal 时优先提取情绪记忆"""
    jiwen_state = jiwen_manager.get_state(character_id)
    if jiwen_state.arousal > 0.3 or abs(jiwen_state.valence) > 0.3:
        # 标记为情绪记忆，后续检索时加权
        memory.emotional_weight = jiwen_state.arousal
```

---

## 六、验证清单

### 6.1 后端验证

- [ ] 启动后端服务，确认 `jiwen_scheduler` 正常启动
- [ ] 调用 `GET /api/jiwen/{character_id}/state` 确认状态查询正常
- [ ] 调用 `POST /api/jiwen/{character_id}/tick` 确认触发器生成
- [ ] 检查 `proactive_messages` 表是否有新记录
- [ ] 调用 `GET /api/jiwen/{character_id}/proactive-messages` 确认消息查询
- [ ] 调用 `POST /api/jiwen/{character_id}/proactive-messages/{id}/consume` 确认消费

### 6.2 前端验证

- [ ] 访问 `/jiwen` 页面，确认角色列表加载
- [ ] 点击角色，确认五轴状态展示
- [ ] 点击"手动 Tick"，确认状态更新
- [ ] 确认主动消息队列展示
- [ ] 点击"标记已读"，确认消息状态变化
- [ ] 确认调度器控制按钮正常工作

### 6.3 集成验证

- [ ] 进行一轮聊天，确认 `post_chat_hooks` 调用 `apply_delta`
- [ ] 检查聊天后 jiwen 状态是否变化
- [ ] 等待调度器 tick，确认触发器生成
- [ ] 检查 contact 触发器是否生成主动消息

---

## 七、经验总结

### 7.1 架构设计经验

1. **触发器消费机制**: 
   - 触发器生成和消费应该解耦
   - 使用消息队列模式（写入表 → 前端轮询）比直接推送更简单可靠

2. **前端状态管理**:
   - 复杂状态用独立页面展示，避免污染主流程
   - 30 秒轮询间隔在实时性和性能之间取得平衡

3. **数据库设计**:
   - 外键关系要清晰（character_id, trigger_id）
   - 索引要覆盖常用查询路径（character_id + created_at）

### 7.2 踩坑教训

1. **模型定义遗漏**:
   - 注释中提到的表必须实际定义
   - 启动时 `Base.metadata.create_all` 只会创建已定义的模型

2. **API 封装不完整**:
   - 前端组件依赖 API 方法，必须同步开发
   - 建议在 `realApi.js` 中集中管理所有 API 方法

3. **路由配置遗漏**:
   - 新页面必须配置路由（routes.js + lazyPages.js）
   - 建议在创建页面时同步配置路由

### 7.3 最佳实践

1. **集成新模块时**:
   - 先完成后端（模型 + API + 业务逻辑）
   - 再完成前端（API 封装 + 组件 + 路由）
   - 最后验证（端到端测试）

2. **触发器消费设计**:
   - 根据业务状态生成不同的消息内容（避免单调）
   - 提供消费机制（避免重复展示）

3. **前端展示设计**:
   - 状态可视化（进度条 + 描述文字）
   - 提供手动操作入口（tick/consume）
   - 自动刷新 + 手动刷新结合

---

## 八、后续优化建议

1. **WebSocket 推送**: 
   - 替代轮询，实时推送主动消息
   - 减少网络请求和服务器负载

2. **情绪记忆加权**:
   - 高 arousal 时优先检索情绪强烈的记忆
   - 情绪一致性检索（valence 匹配）

3. **世界事件影响**:
   - 天气变化影响角色情绪
   - 事件发生触发情绪波动

4. **前端交互增强**:
   - 主动消息弹窗提醒
   - 情绪状态变化动画
   - 触发器时间线展示

5. **性能优化**:
   - 触发器批量处理
   - 消息分页加载
   - 状态缓存策略

---

## 九、参考资源

- Jiwen 引擎源码: `backend/jiwen/jiwen_core.py`
- API 文档: `http://localhost:8000/docs`
- 前端组件: `web/react-vite/src/components/JiwenStatePanel.jsx`
- 控制面板: `web/react-vite/src/pages/JiwenPage.jsx`

---

**总结**: Jiwen 情绪引擎的核心功能已完成集成，主要缺失的是触发器消费机制和前端展示层。通过本次集成，系统已具备完整的情绪管理、触发器生成、主动消息推送能力。后续可继续完成世界引擎和记忆模块的集成，让角色更加"鲜活"。
