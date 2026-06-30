# Grill Me: Jiwen 情绪引擎与对话系统集成经验总结

**日期**: 2026-06-28  
**项目**: CharacterSeed  
**主题**: Jiwen 情绪引擎变化 + 主动消息队列与对话系统集成  
**方法**: 使用 grill-me 技能进行深度拷问和决策梳理

---

## 一、项目背景

### 1.1 CharacterSeed 系统概述
CharacterSeed 是一个 AI 角色养成系统，采用前后端分离架构：
- **后端**: FastAPI + SQLite + SQLAlchemy
- **前端**: React + Vite + TypeScript
- **核心功能**: 角色创建、聊天互动、成长系统、记忆系统、事件系统、Jiwen 情绪引擎

### 1.2 Jiwen 情绪引擎定位
Jiwen 是一个情绪引擎模块，管理角色的五轴连续情绪状态：
- **connection**: 连接需求（0-1）
- **pride**: 骄傲/自尊（-1 到 1）
- **valence**: 情绪效价（-1 到 1）
- **arousal**: 唤醒度（-1 到 1）
- **immersion**: 沉浸度（0-1）

核心特性：
- 数学漂移 + 阈值触发，不依赖概率骰子
- 支持 LLM prompt 注入和风格指引
- 后台调度器周期性推进状态
- 触发器生成主动消息队列

---

## 二、集成目标与决策树

### 2.1 核心问题
**用户提出的需求**: "Jiwen 的变化还有主动发的消息队列需要真正与对话集成在一起"

### 2.2 决策分支拆解
通过 grill-me 技能，将问题拆解为以下决策分支：

#### 分支 1: 数据流集成
- **问题**: Jiwen 状态变化如何影响对话内容？
- **决策**: 通过 LLM prompt 注入和 style guidance 实现
- **实现**: 
  - `interaction.py` 节点 1.5 注入 Director prompt
  - `interaction.py` 节点 4 注入 Actor style guidance

#### 分支 2: 触发器消费机制
- **问题**: contact 触发器生成后如何转化为主动消息？
- **决策**: 使用消息队列模式（写入表 → 前端轮询）
- **实现**:
  - `ProactiveMessage` 模型定义
  - `_handle_contact_triggers()` 方法生成消息
  - 前端 30 秒轮询获取未消费消息

#### 分支 3: 前端展示层
- **问题**: 用户如何感知和交互情绪状态？
- **决策**: 独立控制面板页面 + 状态展示组件
- **实现**:
  - `JiwenPage.jsx` 控制面板
  - `JiwenStatePanel.jsx` 五轴状态可视化
  - 主动消息队列展示和标记已读

#### 分支 4: 状态持久化
- **问题**: 情绪状态如何跨会话保持？
- **决策**: SQLite 数据库 + 单例管理器缓存
- **实现**:
  - `JiwenState` 表存储五轴状态
  - `JiwenManager` 单例管理 per-character 引擎
  - `on_save`/`on_load` 回调实现持久化

#### 分支 5: 后台调度策略
- **问题**: 如何平衡实时性和性能？
- **决策**: 5 分钟间隔 + 降级/恢复机制
- **实现**:
  - `JiwenBackgroundScheduler` 单例调度器
  - 支持 normal/degraded/recovery 三种模式
  - 关键模块保护（拒绝完全停止）

---

## 三、踩坑与解决方案

### 3.1 ProactiveMessage 模型未定义

**问题描述**:
- 设计文档中提到 `proactive_messages` 表
- 但 `models.py` 中只有注释，没有实际定义
- 导致 `Base.metadata.create_all()` 不会创建该表

**根因分析**:
- 开发时只关注了 `JiwenState` 和 `JiwenTrigger` 模型
- `ProactiveMessage` 被当作"后续实现"遗留
- 代码审查时遗漏了模型定义

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

**经验教训**:
- ✅ 注释中提到的表必须实际定义
- ✅ 启动时 `Base.metadata.create_all()` 只会创建已定义的模型
- ✅ 代码审查清单应包含"所有提到的数据模型是否已定义"

---

### 3.2 Contact 触发器没有消费逻辑

**问题描述**:
- `tick_character()` 会调用 `persist_triggers()` 落库
- 但 contact 触发器没有后续处理
- 前端无法接收主动消息

**根因分析**:
- 触发器生成和消费是解耦的两个步骤
- 初期只实现了"生成"，忽略了"消费"
- 没有明确的消息队列设计

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

**经验教训**:
- ✅ 触发器生成和消费应该解耦
- ✅ 使用消息队列模式（写入表 → 前端轮询）比直接推送更简单可靠
- ✅ 情绪状态 → 消息内容的映射要多样化（避免单调）

---

### 3.3 前端缺少 Jiwen API 方法

**问题描述**:
- `realApi.js` 中没有 Jiwen 相关的 API 封装
- 前端组件无法调用后端接口
- 导致 JiwenPage 和 JiwenStatePanel 无法工作

**根因分析**:
- 后端 API 开发完成后，前端 API 封装被遗漏
- 没有同步开发前后端接口

**解决方案**:
在 `web/react-vite/src/utils/realApi.js` 中添加完整的 Jiwen API 方法：
```javascript
// Jiwen 相关 API
getJiwenState: (characterId) => api.get(`/jiwen/${characterId}/state`),
getJiwenTriggers: (characterId, limit = 20) => api.get(`/jiwen/${characterId}/triggers?limit=${limit}`),
tickJiwen: (characterId, ticks = 1) => api.post(`/jiwen/${characterId}/tick?ticks=${ticks}`),
getJiwenSchedulerStatus: () => api.get('/jiwen/scheduler/status'),
startJiwenScheduler: (intervalSeconds = 300) => api.post(`/jiwen/scheduler/start?interval_seconds=${intervalSeconds}`),
stopJiwenScheduler: () => api.post('/jiwen/scheduler/stop'),
tickAllJiwen: () => api.post('/jiwen/scheduler/tick'),
getProactiveMessages: (characterId, limit = 10, unconsumedOnly = true) => 
  api.get(`/jiwen/${characterId}/proactive-messages?limit=${limit}&unconsumed_only=${unconsumedOnly}`),
consumeProactiveMessage: (characterId, messageId) => 
  api.post(`/jiwen/${characterId}/proactive-messages/${messageId}/consume`),
```

**经验教训**:
- ✅ 前端 API 封装必须与后端同步开发
- ✅ 建议在 `realApi.js` 中集中管理所有 API 方法
- ✅ 提供完整的 API 文档（Swagger/OpenAPI）便于前端对接

---

### 3.4 前端路由未配置

**问题描述**:
- `JiwenPage.jsx` 创建后无法访问
- 浏览器访问 `/jiwen` 返回 404

**根因分析**:
- 新页面创建后忘记配置路由
- React Router 需要显式声明路由

**解决方案**:
1. 在 `web/react-vite/src/router/routes.js` 中添加路由配置：
```javascript
{ path: '/jiwen', title: '积温', pageKey: 'jiwen', showInNav: true }
```

2. 在 `web/react-vite/src/router/lazyPages.js` 中添加懒加载：
```javascript
jiwen: lazy(() => import('../pages/JiwenPage'))
```

**经验教训**:
- ✅ 新页面必须配置路由（routes.js + lazyPages.js）
- ✅ 建议在创建页面时同步配置路由
- ✅ 使用 `showInNav: true` 控制是否在导航栏显示

---

### 3.5 情绪状态 → 消息内容映射单调

**问题描述**:
- 初期实现中，所有 contact 触发器都生成相同的消息
- 用户体验单调，缺乏情绪一致性

**根因分析**:
- 没有根据 Jiwen 状态动态生成消息内容
- 忽略了情绪状态对表达方式的影响

**解决方案**:
根据 connection 和 pride 的组合生成不同的开场白：
- 高 connection + 低 pride → 温和开场："在忙吗？想找你聊聊。"
- 高 connection + 高 pride → 傲娇开场："（嘴硬地）人呢？怎么不说话了？"
- 中 connection + 低 pride → 普通问候："最近怎么样？"
- 中 connection + 高 pride → 犹豫开场："（犹豫了一下）...在吗？"
- 低 connection → 直接开场："嘿，有空吗？"

**经验教训**:
- ✅ 情绪状态应该影响消息内容和语气
- ✅ 多样化的表达方式提升用户体验
- ✅ 可以后续引入 LLM 动态生成更丰富的消息

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

**数据流**:
```
JiwenEngine.tick()
  → 检测阈值
  → 生成触发器 [{action: "contact", state_at_trigger: {...}}]
  
JiwenManager.tick_character()
  → 调用 _persist_triggers() 落库
  → 调用 _handle_contact_triggers() 生成消息
  → 写入 ProactiveMessage 表
  
前端 JiwenPage
  → 30 秒轮询 getProactiveMessages()
  → 展示未消费消息
  → 用户点击"标记已读"
  → 调用 consumeProactiveMessage()
```

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

**性能考虑**:
- 30 秒轮询间隔在实时性和性能间取得平衡
- 避免频繁请求导致服务器压力
- 后续可考虑 WebSocket 推送替代轮询

### 4.3 数据库表设计

**关键索引**:
- `proactive_messages.character_id`: 按角色查询消息
- `proactive_messages.created_at`: 按时间排序
- `proactive_messages.consumed`: 过滤未消费消息

**外键关系**:
- `character_id` → `characters.id` (CASCADE DELETE)
- `trigger_id` → `jiwen_triggers.id` (可选，用于追溯触发源)

**字段设计**:
- `consumed`: 使用 Integer(0/1) 而非 Boolean，便于查询和索引
- `created_at`: 使用 `server_default=func.now()` 确保数据库层面的一致性

### 4.4 后台调度器设计

**单例模式**:
```python
class JiwenBackgroundScheduler:
    _instance: Optional["JiwenBackgroundScheduler"] = None
    _lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> "JiwenBackgroundScheduler":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
```

**降级/恢复机制**:
- `normal` 模式: 5 分钟间隔（默认）
- `degraded` 模式: 15 分钟间隔（系统高负载时使用）
- `recovery` 模式: 恢复到 5 分钟间隔

**关键模块保护**:
- `is_critical = True` 标记为关键模块
- `stop()` 方法拒绝完全停止关键模块
- 只能降级，不能停止

---

## 五、未完成项与后续优化

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

### 5.4 WebSocket 推送

**现状**:
- 前端使用 30 秒轮询获取主动消息
- 实时性有限，且增加服务器负载

**建议方案**:
- 使用 WebSocket 替代轮询
- 后端主动推送新消息
- 减少网络请求和服务器压力

### 5.5 LLM 动态生成消息

**现状**:
- 主动消息内容基于固定模板
- 缺乏个性化和情境适应性

**建议方案**:
- 调用 LLM 根据 Jiwen 状态 + 角色设定 + 最近对话动态生成消息
- 提升消息的自然度和个性化

---

## 六、验证清单

### 6.1 后端验证

- [x] 启动后端服务，确认 `jiwen_scheduler` 正常启动
- [x] 调用 `GET /api/jiwen/{character_id}/state` 确认状态查询正常
- [x] 调用 `POST /api/jiwen/{character_id}/tick` 确认触发器生成
- [x] 检查 `proactive_messages` 表是否有新记录
- [x] 调用 `GET /api/jiwen/{character_id}/proactive-messages` 确认消息查询
- [x] 调用 `POST /api/jiwen/{character_id}/proactive-messages/{id}/consume` 确认消费

### 6.2 前端验证

- [x] 访问 `/jiwen` 页面，确认角色列表加载
- [x] 点击角色，确认五轴状态展示
- [x] 点击"手动 Tick"，确认状态更新
- [x] 确认主动消息队列展示
- [x] 点击"标记已读"，确认消息状态变化
- [x] 确认调度器控制按钮正常工作

### 6.3 集成验证

- [x] 进行一轮聊天，确认 `post_chat_hooks` 调用 `apply_delta`
- [x] 检查聊天后 jiwen 状态是否变化
- [x] 等待调度器 tick，确认触发器生成
- [x] 检查 contact 触发器是否生成主动消息

---

## 七、经验总结

### 7.1 架构设计经验

1. **触发器消费机制**:
   - 触发器生成和消费应该解耦
   - 使用消息队列模式（写入表 → 前端轮询）比直接推送更简单可靠

2. **前端状态管理**:
   - 复杂状态用独立页面展示，避免污染主流程
   - 30 秒轮询间隔在实时性和性能间取得平衡

3. **数据库设计**:
   - 外键关系要清晰（character_id, trigger_id）
   - 索引要覆盖常用查询路径（character_id + created_at）

4. **后台调度器**:
   - 单例模式确保全局唯一
   - 降级/恢复机制提升系统韧性
   - 关键模块保护避免误操作

### 7.2 踩坑教训

1. **模型定义遗漏**:
   - 注释中提到的表必须实际定义
   - 启动时 `Base.metadata.create_all` 只会创建已定义的模型
   - 代码审查清单应包含"所有提到的数据模型是否已定义"

2. **API 封装不完整**:
   - 前端组件依赖 API 方法，必须同步开发
   - 建议在 `realApi.js` 中集中管理所有 API 方法
   - 提供完整的 API 文档便于前端对接

3. **路由配置遗漏**:
   - 新页面必须配置路由（routes.js + lazyPages.js）
   - 建议在创建页面时同步配置路由
   - 使用 `showInNav: true` 控制导航栏显示

4. **触发器消费逻辑缺失**:
   - 触发器生成和消费是解耦的两个步骤
   - 必须明确消息队列设计
   - 情绪状态 → 消息内容的映射要多样化

### 7.3 最佳实践

1. **集成新模块时**:
   - 先完成后端（模型 + API + 业务逻辑）
   - 再完成前端（API 封装 + 组件 + 路由）
   - 最后验证（端到端测试）

2. **触发器消费设计**:
   - 根据业务状态生成不同的消息内容（避免单调）
   - 提供消费机制（避免重复展示）
   - 考虑后续引入 LLM 动态生成

3. **前端展示设计**:
   - 状态可视化（进度条 + 描述文字）
   - 提供手动操作入口（tick/consume）
   - 自动刷新 + 手动刷新结合

4. **代码审查清单**:
   - 所有提到的数据模型是否已定义？
   - 前端 API 封装是否完整？
   - 新页面是否配置路由？
   - 触发器消费逻辑是否实现？

---

## 八、grill-me 技能使用心得

### 8.1 技能价值

1. **决策拆解**:
   - 将模糊需求拆解为清晰的决策分支
   - 每个分支独立解决，降低复杂度

2. **深度拷问**:
   - 挑战每个决策的合理性
   - 暴露隐藏的假设和风险

3. **经验沉淀**:
   - 将踩坑和解决方案系统化记录
   - 形成可复用的最佳实践

### 8.2 使用建议

1. **适用场景**:
   - 复杂功能集成（如 Jiwen 情绪引擎）
   - 架构设计决策（如触发器消费机制）
   - 多模块协作（如前后端集成）

2. **使用流程**:
   - 明确主题和目标
   - 拆解决策分支
   - 逐个拷问和解决
   - 记录经验和教训

3. **注意事项**:
   - 不要跳过任何分支
   - 每个决策都要有明确的理由
   - 经验总结要具体可操作

---

## 九、参考资源

- Jiwen 引擎源码: `backend/jiwen/jiwen_core.py`
- 管理器实现: `backend/jiwen/jiwen_manager.py`
- 调度器实现: `backend/jiwen/jiwen_scheduler.py`
- API 文档: `http://localhost:8000/docs`
- 前端组件: `web/react-vite/src/components/JiwenStatePanel.jsx`
- 控制面板: `web/react-vite/src/pages/JiwenPage.jsx`
- 集成总结: `docs/2026-06-28-jiwen-integration-summary.md`

---

**总结**: 通过 grill-me 技能的深度拷问，我们系统性地解决了 Jiwen 情绪引擎与对话系统集成的核心问题。主要成果包括：
1. 完善了触发器消费机制（contact → 主动消息队列）
2. 实现了前端展示层（控制面板 + 状态可视化）
3. 沉淀了集成新模块的最佳实践

后续可继续完成世界引擎、记忆模块集成，以及 WebSocket 推送和 LLM 动态生成等优化，让角色更加"鲜活"。
