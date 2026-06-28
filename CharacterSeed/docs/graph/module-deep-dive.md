# CharacterSeed 模块深度分析

> 基于 CodeGraph 语义分析，194 文件，3,463 节点，7,365 条边

---

## 1. 记忆系统调用链

### 1.1 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    ContextManager                           │
│  (backend/memory/context_manager.py)                        │
│  - 协调短期/长期/知识库三层记忆                                │
│  - 构建 LLM 上下文                                          │
└────────────────┬────────────────────────────────────────────┘
                 │
    ┌────────────┼────────────┬────────────────┐
    │            │            │                │
    ▼            ▼            ▼                ▼
┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
│ShortTerm│ │ LongTerm │ │Knowledge │ │   Database   │
│ Memory  │ │  Memory  │ │  Base    │ │  (SQLite)    │
│(滑动窗口)│ │(语义检索) │ │(RAG/cognee)│ │              │
└─────────┘ └──────────┘ └──────────┘ └──────────────┘
```

### 1.2 核心组件

#### ShortTermMemory (`backend/memory/short_term.py`)
- **职责**：管理最近 K 轮对话（默认 10 轮 = 20 条消息）
- **数据结构**：`collections.deque(maxlen=k*2)` 自动维护窗口
- **特点**：
  - 不持久化，重启即丢失
  - 即时访问，无延迟
  - 会话结束后自动清理

```python
class ShortTermMemory:
    def __init__(self, k: int = 10, session_id: Optional[str] = None):
        self.k = k
        self._messages: deque = deque(maxlen=k * 2)
    
    def add_user_message(self, message: str) -> None:
        self._messages.append(("user", message))
    
    def add_ai_message(self, message: str) -> None:
        self._messages.append(("assistant", message))
    
    def get_message_list(self) -> List[Dict[str, str]]:
        """返回 OpenAI 格式: [{"role": "user", "content": "..."}]"""
        return [{"role": role, "content": content} for role, content in self._messages]
```

#### LongTermMemory (`backend/memory/long_term.py`)
- **职责**：语义检索重要对话，支持持久化
- **特点**：
  - 基于嵌入向量检索
  - 重要内容从短期记忆提升
  - 跨会话保留

#### KnowledgeBase (`backend/memory/knowledge_base.py`)
- **职责**：RAG 知识库，支持角色背景、世界设定等
- **实现**：
  - 主方案：cognee（异步语义检索）
  - 降级方案：本地文件 + 关键词匹配

```python
class KnowledgeBase:
    def __init__(self, dataset_name: str = "character_knowledge", use_cognee: bool = True):
        self.use_cognee = use_cognee and COGNEE_AVAILABLE
        if not self.use_cognee:
            self._init_fallback()  # 降级到本地文件存储
    
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        if self.use_cognee:
            return await cognee.search(query_text=query, datasets=[self.dataset_name])
        else:
            return self._search_fallback(query, limit)  # 关键词匹配
```

#### ContextManager (`backend/memory/context_manager.py`)
- **职责**：协调三层记忆，构建完整 LLM 上下文
- **核心方法**：

```python
class ContextManager:
    def __init__(self, character_id: int, user_id: str, max_tokens: int = 4000):
        self.short_term = ShortTermMemory(k=10)
        self.long_term = LongTermMemory(...)
        self.knowledge = KnowledgeBase(dataset_name=f"char_{character_id}")
    
    def add(self, user_message: str, ai_message: str, promote_to_long_term: bool = True):
        """添加一轮对话到记忆系统"""
        # 1. 添加到短期记忆
        self.short_term.add_user_message(user_message)
        self.short_term.add_ai_message(ai_message)
        
        # 2. 可选：提升到长期记忆
        if promote_to_long_term:
            content = f"用户: {user_message}\n角色: {ai_message}"
            self.long_term.add(content, metadata={"type": "conversation"})
    
    def build_context(self, current_query: str, include_short_term=True, 
                      include_long_term=True, include_knowledge=True) -> Dict[str, Any]:
        """构建完整的 LLM 上下文"""
        context = {"query": current_query, "short_term": [], "long_term": [], "knowledge": []}
        
        # 1. 短期记忆（最近 K 轮）
        if include_short_term:
            context["short_term"] = self.short_term.get_message_list()
        
        # 2. 长期记忆（语义相关）
        if include_long_term:
            context["long_term"] = self.long_term.search(current_query, limit=self.long_term_limit)
        
        # 3. 知识库（RAG 检索）
        if include_knowledge:
            context["knowledge"] = asyncio.run(self.knowledge.search(current_query))
        
        # 4. Token 估算
        context["metadata"]["token_estimate"] = self._estimate_tokens(context)
        return context
    
    def format_for_prompt(self, context: Dict[str, Any], template: str = "default") -> str:
        """将上下文格式化为 Prompt 文本"""
        # 默认格式：【近期对话】+【相关记忆】+【相关知识】
        return self._format_default(context)
```

### 1.3 调用流程

```
用户发送消息
    │
    ▼
chat_router.py: chat_stream()
    │
    ├─► get_pipeline().run_stream()
    │       │
    │       ├─► ContextManager.add(user_message, ai_message)
    │       │       │
    │       │       ├─► short_term.add_user_message()
    │       │       ├─► short_term.add_ai_message()
    │       │       └─► long_term.add() [if promote_to_long_term]
    │       │
    │       └─► ContextManager.build_context(current_query)
    │               │
    │               ├─► short_term.get_message_list()
    │               ├─► long_term.search(query)
    │               └─► knowledge.search(query)
    │
    └─► 返回 SSE 流式响应
```

### 1.4 关键设计决策

| 决策 | 原因 |
|------|------|
| 短期记忆用 deque | 自动维护窗口大小，O(1) 插入/删除 |
| K=10 轮 | 平衡上下文覆盖（5-8 轮完整对话）和 Token 预算（~2000 tokens） |
| 长期记忆可选提升 | 避免所有对话都入库，只保留重要内容 |
| KnowledgeBase 降级方案 | cognee 不可用时用关键词匹配，保证可用性 |
| Token 估算 | 中文 1 token ≈ 1.5 字符，粗略但够用 |

---

## 2. Jiwen 情绪引擎调用链

### 2.1 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                   JiwenScheduler                            │
│  (backend/jiwen/jiwen_scheduler.py)                         │
│  - APScheduler 定期调度                                      │
│  - 每 N 分钟调用 manager.tick()                              │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│                   JiwenManager                              │
│  (backend/jiwen/jiwen_manager.py)                           │
│  - 封装 JiwenCore，提供高级 API                              │
│  - 管理多角色实例                                            │
│  - 持久化到数据库                                            │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│                    JiwenCore                                │
│  (backend/jiwen/jiwen_core.py)                              │
│  - 核心状态机，5 维度情绪模型                                  │
│  - connection / pride / valence / arousal / immersion        │
│  - tick() 推进状态漂移                                       │
│  - _check_thresholds() 检测触发器                            │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

#### JiwenCore (`backend/jiwen/jiwen_core.py`)
- **职责**：核心状态机，管理 5 个情绪维度
- **状态维度**：

| 维度 | 范围 | 含义 | 漂移规则 |
|------|------|------|----------|
| `connection` | [0, 1] | 连接需求/积温 | 随时间增长，高 connection 时加速 |
| `pride` | [-1, 1] | 骄傲/自尊 | 朝 0 回归，高 connection 时防御性上升 |
| `valence` | [-1, 1] | 情绪效价（心情） | 朝 setpoint 回归，高 connection 时减速 |
| `arousal` | [-1, 1] | 唤醒度（焦躁） | 朝 0 回归，高 connection 时攀升 |
| `immersion` | [0, 1] | 沉浸度 | 衰减，activity 时提升 |

```python
class JiwenCore:
    def __init__(self, character_id: int, get_last_message=None, 
                 connection_rate_fn=None, on_save=None, on_load=None):
        self.character_id = character_id
        self._state = JiwenStateSnapshot()  # 5 维度状态
        self._lock = threading.RLock()
        
        # 回调函数
        self.get_last_message = get_last_message  # 获取最后一条消息
        self.connection_rate_fn = connection_rate_fn  # 连接增长速率
        self.on_save = on_save  # 持久化回调
        self.on_load = on_load  # 加载回调
    
    def tick(self, minutes: float) -> List[Dict[str, Any]]:
        """推进状态漂移，返回触发器列表"""
        with self._lock:
            state = self._state
            
            # 1. connection: 动态增长曲线
            base_rate = self.connection_rate_fn(last_msg)
            accel_factor = (1.0 + rates["connectionAccel"]) ** eff_minutes
            valence_factor = ...  # 根据 valence 调整
            delta_connection = base_rate * accel_factor * valence_factor * minutes
            state.connection = _clip(state.connection + delta_connection, 0, 1)
            
            # 2. pride: 朝 0 回归；高 connection 时防御性上升
            if state.connection > rates["prideDefendThreshold"]:
                delta_pride = (target - state.pride) * rates["prideDefendRate"] * minutes
            else:
                delta_pride = -state.pride * rates["prideRegression"] * minutes
            state.pride = _clip(state.pride + delta_pride, -1, 1)
            
            # 3. valence: 朝 setpoint 回归
            delta_valence = (setpoint - state.valence) * rates["valenceRegression"] * minutes
            state.valence = _clip(state.valence + delta_valence, -1, 1)
            
            # 4. arousal: 朝 0 回归；高 connection 时攀升
            if state.connection > rates["arousalConnectionRiseThresh"]:
                delta_arousal = rates["arousalConnectionRiseRate"] * minutes
            else:
                delta_arousal = -state.arousal * rates["arousalRegression"] * minutes
            state.arousal = _clip(state.arousal + delta_arousal, -1, 1)
            
            # 5. immersion: 衰减
            delta_immersion = -rates["immersionDecay"] * minutes
            state.immersion = _clip(state.immersion + delta_immersion, 0, 1)
            
            # 6. 触发器检测
            triggers = self._check_thresholds()
            return triggers
    
    def _check_thresholds(self) -> List[Dict[str, Any]]:
        """检测阈值，返回触发器"""
        triggers = []
        c, p, v, a = state.connection, state.pride, state.valence, state.arousal
        
        # observation: 注意到沉默
        if c >= thresholds["observation"]:
            triggers.append({"action": "observation", "reason": f"connection {c:.2f} >= observation"})
        
        # contact: 主动联系
        if c >= thresholds["forceContact"]:
            triggers.append({"action": "contact", "forced": True})
        elif c >= thresholds["considerContact"] and p < thresholds["prideBlock"]:
            triggers.append({"action": "contact", "forced": False})
        elif c >= thresholds["considerContact"] and p >= thresholds["prideBlock"]:
            triggers.append({"action": "find_activity", "reason": "想开口但骄傲阻断"})
        
        # find_activity: 自我调节（心情差或焦躁）
        if v <= thresholds["valenceActivity"]:
            triggers.append({"action": "find_activity", "reason": "心情差，自我调节"})
        if a >= thresholds["arousalAgitation"]:
            triggers.append({"action": "find_activity", "reason": "焦躁，宣泄"})
        
        return triggers
    
    def apply_delta(self, delta: Dict[str, float]):
        """聊天后调整状态（情绪更新）"""
        for axis, val in delta.items():
            if axis in self.axes:
                lo, hi = self.axes[axis]
                cur = getattr(state, axis)
                new = _clip(cur + val, lo, hi)
                setattr(state, axis, new)
    
    def get_prompt_context(self) -> str:
        """生成 LLM 用的状态自然语言描述"""
        return f"[积温] c:{state.connection:.2f}({'悠闲' if c<0.2 else '想开口' if c<0.5 else '坐不住'}) ..."
    
    def get_style_guidance(self) -> str:
        """生成 LLM 用的说话风格指引"""
        return f"语气：{'放软' if p<0 else '端着' if p>0.3 else '中性'}，{'低落' if v<-0.3 else '开心' if v>0.3 else '平静'}..."
```

#### JiwenManager (`backend/jiwen/jiwen_manager.py`)
- **职责**：封装 JiwenCore，提供高级 API，管理多角色实例
- **核心方法**：

```python
class JiwenManager:
    def __init__(self):
        self._cores: Dict[int, JiwenCore] = {}  # character_id -> JiwenCore
    
    def get_or_create(self, character_id: int) -> JiwenCore:
        """获取或创建角色的 JiwenCore 实例"""
        if character_id not in self._cores:
            core = JiwenCore(
                character_id=character_id,
                get_last_message=lambda: self._get_last_message(character_id),
                connection_rate_fn=lambda msg: self._calc_connection_rate(msg),
                on_save=lambda data: self._save_state(character_id, data),
                on_load=lambda: self._load_state(character_id),
            )
            core.load()  # 从数据库加载历史状态
            self._cores[character_id] = core
        return self._cores[character_id]
    
    def tick_all(self, minutes: float) -> Dict[int, List[Dict]]:
        """对所有角色推进状态漂移"""
        results = {}
        for char_id, core in self._cores.items():
            triggers = core.tick(minutes)
            if triggers:
                results[char_id] = triggers
        return results
    
    def apply_chat_delta(self, character_id: int, delta: Dict[str, float]):
        """聊天后调整角色情绪状态"""
        core = self.get_or_create(character_id)
        core.apply_delta(delta)
        core.save()  # 持久化到数据库
    
    def get_state(self, character_id: int) -> Dict[str, Any]:
        """获取角色当前情绪状态"""
        core = self.get_or_create(character_id)
        return core.get_state()
```

#### JiwenScheduler (`backend/jiwen/jiwen_scheduler.py`)
- **职责**：APScheduler 定期调度，推进状态漂移
- **调度策略**：

```python
class JiwenScheduler:
    def __init__(self, manager: JiwenManager):
        self.manager = manager
        self.scheduler = BackgroundScheduler()
        
        # 每 5 分钟 tick 一次
        self.scheduler.add_job(
            self._tick_job,
            'interval',
            minutes=5,
            id='jiwen_tick',
            replace_existing=True,
        )
    
    def start(self):
        self.scheduler.start()
    
    def _tick_job(self):
        """定期任务：推进所有角色的状态漂移"""
        # 计算距上次 tick 的时间间隔
        now = datetime.now()
        last_tick = self._get_last_tick_time()
        minutes = (now - last_tick).total_seconds() / 60 if last_tick else 5.0
        
        # 推进状态漂移
        triggers_by_char = self.manager.tick_all(minutes)
        
        # 处理触发器
        for char_id, triggers in triggers_by_char.items():
            for trigger in triggers:
                self._handle_trigger(char_id, trigger)
        
        self._set_last_tick_time(now)
    
    def _handle_trigger(self, character_id: int, trigger: Dict[str, Any]):
        """处理触发器：执行对应动作"""
        action = trigger["action"]
        
        if action == "contact":
            # 主动联系：生成主动消息
            self._generate_proactive_message(character_id, trigger)
        
        elif action == "find_activity":
            # 自我调节：记录日志，不主动联系
            logger.info(f"角色 {character_id} 触发 find_activity: {trigger['reason']}")
        
        elif action == "observation":
            # 注意到沉默：轻微提升 arousal
            core = self.manager.get_or_create(character_id)
            core.apply_delta({"arousal": 0.05})
```

### 2.3 调用流程

```
APScheduler (每 5 分钟)
    │
    ▼
JiwenScheduler._tick_job()
    │
    ├─► JiwenManager.tick_all(minutes)
    │       │
    │       └─► JiwenCore.tick(minutes) [for each character]
    │               │
    │               ├─► 状态漂移（connection/pride/valence/arousal/immersion）
    │               │
    │               └─► _check_thresholds()
    │                       │
    │                       └─► 返回触发器列表
    │
    └─► JiwenScheduler._handle_trigger(char_id, trigger)
            │
            ├─► action="contact" → 生成主动消息
            ├─► action="find_activity" → 记录日志
            └─► action="observation" → 轻微提升 arousal

聊天后情绪更新
    │
    ▼
chat_router.py: chat_stream()
    │
    └─► post_chat.py: update_emotion_after_chat()
            │
            └─► JiwenManager.apply_chat_delta(character_id, delta)
                    │
                    └─► JiwenCore.apply_delta(delta)
                            │
                            └─► JiwenCore.save() → 持久化到数据库
```

### 2.4 触发器阈值

| 触发器 | 条件 | 动作 |
|--------|------|------|
| `observation` | connection ≥ 0.2 | 注意到沉默，轻微提升 arousal |
| `considerContact` | connection ≥ 0.4 | 考虑主动联系 |
| `forceContact` | connection ≥ 0.5 | 强制主动联系 |
| `prideBlock` | pride ≥ 0.3 | 骄傲阻断，转为 find_activity |
| `valenceActivity` | valence ≤ -0.3 | 心情差，自我调节 |
| `arousalAgitation` | arousal ≥ 0.3 | 焦躁，宣泄 |

### 2.5 关键设计决策

| 决策 | 原因 |
|------|------|
| 5 维度情绪模型 | 覆盖连接、自尊、心情、焦躁、沉浸，足够表达复杂情绪 |
| 状态漂移 + 触发器 | 模拟真实情绪变化，不是简单响应 |
| connection 动态增长 | 高 connection 时加速，模拟"想念"效应 |
| pride 防御性上升 | 高 connection 时 pride 朝正方向漂移，模拟"防御心理" |
| 触发器分级 | observation → considerContact → forceContact，渐进式 |
| 聊天后 apply_delta | 情绪不是固定不变，聊天会调整状态 |

---

## 3. 前端 ChatPage 完整依赖树

### 3.1 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      ChatPage.jsx                           │
│  (web/react-vite/src/pages/ChatPage.jsx)                    │
│  - 主聊天页面组件                                            │
│  - 管理会话、消息、流式响应                                    │
└────────────────┬────────────────────────────────────────────┘
                 │
    ┌────────────┼────────────┬────────────────┐
    │            │            │                │
    ▼            ▼            ▼                ▼
┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
│useSessions│ │useCharacters│ │  api.js  │ │  Components  │
│(会话管理) │ │(角色管理) │ │(API 层)  │ │(UI 组件)     │
└─────────┘ └──────────┘ └──────────┘ └──────────────┘
```

### 3.2 核心依赖

#### useSessions (`web/react-vite/src/hooks/useSessions.js`)
- **职责**：会话管理（创建、切换、重命名、删除、消息追加）
- **核心状态**：

```javascript
export function useSessions({ api, activeCharacterId, initialSessions, initialMessages } = {}) {
    const [sessionsByChar, setSessionsByChar] = useState(() => initialSessions || {})
    const [activeSessionId, setActiveSessionIdState] = useState(null)
    const [messagesBySession, setMessagesBySession] = useState(() => initialMessages || {})
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)
    const isStreamingRef = useRef(false)  // 流式状态开关
    
    // 派生状态
    const sessions = activeCharacterId ? (sessionsByChar[activeCharacterId] || []) : []
    const activeSession = sessions.find(s => s.id === activeSessionId) || null
    const messages = activeSessionId ? (messagesBySession[activeSessionId] || []) : []
    
    return {
        sessions,           // 当前角色下所有 sessions
        activeSession,      // 当前活跃 session
        activeSessionId,    // 当前活跃 session ID
        setActiveSessionId, // 切换 session
        messages,           // 当前 session 的消息列表
        refresh,            // 从后端加载 sessions
        createNew,          // 新建 session
        rename,             // 重命名 session
        remove,             // 删除 session
        appendMessage,      // 追加消息
        updateMessage,      // 更新消息（流式）
        setMessages,        // 批量设置消息
        clearMessages,      // 清空消息
        patchSession,       // 局部更新 session（onMeta 用）
        loading,
        error,
        setStreaming,       // 设置流式状态（跳过 localStorage 持久化）
    }
}
```

#### useCharacters (`web/react-vite/src/hooks/useCharacters.js`)
- **职责**：角色管理（列表、选中、刷新、删除）
- **核心状态**：

```javascript
export function useCharacters() {
    const [characters, setCharacters] = useState([])
    const [activeCharacterId, setActiveCharacterId] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)
    
    // 派生状态
    const activeCharacter = characters.find(c => c.id === activeCharacterId) || null
    
    return {
        characters,         // 所有角色列表
        activeCharacterId,  // 当前选中角色 ID
        activeCharacter,    // 当前选中角色对象
        loading,
        error,
        refresh,            // 从后端加载角色列表
        setActiveCharacterId, // 切换角色
        remove,             // 删除角色
    }
}
```

#### api.js → realApi.js (`web/react-vite/src/api/`)
- **职责**：API 层，封装所有后端接口调用
- **核心方法**：

```javascript
// api.js
import { realApi } from './realApi'
export const api = realApi

// realApi.js
export const realApi = {
    // 角色相关
    getCharacters: () => _fetch('/api/characters'),
    getCharacter: (id) => _fetch(`/api/characters/${id}`),
    createCharacter: (data) => _fetch('/api/characters/create', { method: 'POST', body: data }),
    deleteCharacter: (id) => _fetch(`/api/characters/${id}`, { method: 'DELETE' }),
    
    // 会话相关
    getSessions: (characterId) => _fetch(`/api/sessions?character_id=${characterId}`),
    createSession: (characterId, title) => _fetch('/api/sessions', { method: 'POST', body: { character_id: characterId, title } }),
    renameSession: (id, title) => _fetch(`/api/sessions/${id}`, { method: 'PATCH', body: { title } }),
    deleteSession: (id) => _fetch(`/api/sessions/${id}`, { method: 'DELETE' }),
    
    // 聊天相关
    sendMessage: (characterId, message, sessionId) => _fetch('/api/chat/stream', {
        method: 'POST',
        body: { character_id: characterId, message, session_id: sessionId },
    }),
    
    // 记忆相关
    getMemories: (characterId) => _fetch(`/api/characters/${characterId}/memories`),
    getConversations: (characterId) => _fetch(`/api/characters/${characterId}/conversations`),
    getGrowthLogs: (characterId) => _fetch(`/api/characters/${characterId}/growth-logs`),
    
    // 事件相关
    getEvents: (characterId) => _fetch(`/api/characters/${characterId}/events`),
    advanceEvent: (characterId) => _fetch('/api/event/advance', { method: 'POST', body: { character_id: characterId } }),
    
    // Jiwen 相关
    getJiwenState: (characterId) => _fetch(`/api/jiwen/${characterId}/state`),
    
    // 通用请求方法
    _fetch: async (url, options = {}) => {
        const response = await fetch(url, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...options.headers },
            body: options.body ? JSON.stringify(options.body) : undefined,
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }))
            throw new Error(error.detail || '请求失败')
        }
        return response.json()
    },
}
```

#### Components (`web/react-vite/src/components/`)
- **ChatBubble**：消息气泡（用户/AI）
- **SessionPanel**：侧栏会话列表
- **Modal**：模态框
- **Toast**：提示消息
- **StatusDot**：状态指示器
- **EmptyState**：空状态占位

### 3.3 调用流程

```
用户打开 ChatPage
    │
    ▼
ChatPage.jsx: useEffect()
    │
    ├─► useCharacters.refresh() → 加载角色列表
    │
    ├─► useSessions.refresh() → 加载当前角色的 sessions
    │
    └─► 渲染 UI
            │
            ├─► SessionPanel（侧栏）
            │       │
            │       └─► 点击 session → useSessions.setActiveSessionId(id)
            │
            └─► ChatArea（主区域）
                    │
                    ├─► 渲染 messages（来自 useSessions.messages）
                    │
                    └─► 用户发送消息
                            │
                            ▼
                    ChatPage.jsx: handleSendMessage()
                            │
                            ├─► useSessions.appendMessage(sessionId, userMessage)
                            │
                            ├─► api.sendMessage(characterId, message, sessionId)
                            │       │
                            │       └─► POST /api/chat/stream (SSE)
                            │               │
                            │               └─► 流式返回 AI 回复
                            │
                            ├─► useSessions.updateMessage(sessionId, messageId, { content: chunk })
                            │       [逐字更新消息内容]
                            │
                            └─► useSessions.setStreaming(false) → 持久化到 localStorage

用户切换角色
    │
    ▼
ChatPage.jsx: handleCharacterChange(characterId)
    │
    ├─► useCharacters.setActiveCharacterId(characterId)
    │
    └─► useSessions.refresh() → 加载新角色的 sessions
```

### 3.4 关键设计决策

| 决策 | 原因 |
|------|------|
| useSessions 管理消息 | 集中管理会话和消息状态，避免 prop drilling |
| localStorage 持久化 | 刷新页面后保留会话和消息 |
| isStreamingRef 开关 | 流式期间跳过 localStorage 持久化，避免频繁写入 |
| patchSession 方法 | onMeta 回调时局部更新 session title，避免整页 reload |
| _sanitizeTitle 函数 | 清理损坏字符（U+FFFD、连续问号），保证 title 显示正常 |
| _toBackendSessionId / _toFrontendSessionId | 前后端 session ID 格式转换（`sess-123` ↔ `123`） |

---

## 4. 总结

### 4.1 模块关系

```
┌─────────────────────────────────────────────────────────────┐
│                         Frontend                            │
│  ChatPage → useSessions / useCharacters → api.js → realApi  │
└────────────────────────┬────────────────────────────────────┘
                         │ REST / SSE
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                         Backend                             │
│  chat_router → Pipeline → ContextManager → LLMService       │
│                           ↓                                 │
│                    post_chat → JiwenManager.apply_delta()   │
│                           ↓                                 │
│                    JiwenScheduler → JiwenCore.tick()        │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 数据流

1. **用户发送消息** → ChatPage → api.sendMessage → chat_router → Pipeline
2. **Pipeline 处理** → ContextManager.build_context → LLMService.call_stream
3. **流式返回** → SSE → ChatPage → useSessions.updateMessage
4. **聊天后更新** → post_chat → JiwenManager.apply_delta → JiwenCore.apply_delta
5. **定期漂移** → JiwenScheduler.tick → JiwenCore.tick → 触发器 → 主动消息

### 4.3 关键文件清单

| 模块 | 核心文件 |
|------|----------|
| 记忆系统 | `backend/memory/context_manager.py`, `short_term.py`, `long_term.py`, `knowledge_base.py` |
| Jiwen 引擎 | `backend/jiwen/jiwen_core.py`, `jiwen_manager.py`, `jiwen_scheduler.py` |
| 前端 ChatPage | `web/react-vite/src/pages/ChatPage.jsx`, `hooks/useSessions.js`, `hooks/useCharacters.js`, `api/realApi.js` |
