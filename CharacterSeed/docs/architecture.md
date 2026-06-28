# CharacterSeed 架构文档

> 版本：2026-06-27
> 状态：living document
> 配套：[2026-06-27-jiwen-integration-design.md](./superpowers/plans/2026-06-27-jiwen-integration-design.md)（jiwen 详细设计）
> 配套：[2026-06-27-world-pillar-design.md](./superpowers/plans/2026-06-27-world-pillar-design.md)（世界四要素实施设计 ADR-009）

***

## 0. 项目愿景（创始人原始表述）

> **一套基于人格、世界、时间和记忆驱动的 NPC 生命系统，为游戏角色提供持续存在、自主成长和长期演化能力。**

四个并列驱动要素（**Personality / World / Time / Memory**）共同决定 NPC 的"生命感"：

| 要素 | 英文 | 核心职责 | 关键问题 |
|------|------|---------|---------|
| **人格** | Personality | 性格、价值观、说话方式、情绪反应 | "这个角色是**谁**？" |
| **世界** | World | 世界设定、当前场景、空间位置、事件发生的舞台 | "这个角色在**哪里**？" |
| **时间** | Time | 时间推进、日程安排、季节/天气、生命周期 | "这个角色在**什么时候**做什么？" |
| **记忆** | Memory | 经验存储、选择性遗忘、回忆唤醒、跨时间检索 | "这个角色**记得/忘了**什么？" |

> **设计目标**：四要素**缺一不可**——少人格则"千人一面"，少世界则"无处容身"，少时间则"瞬间永恒"，少记忆则"金鱼脑"。

> 注：v0.1 文档曾把项目重定位为"AI 角色**陪伴**系统"，弱化了"游戏 NPC"语境，2026-06-27 经发起人确认**回归创始人原始表述**。本节为**愿景层（WHY）**，具体实现见 §1 顶层架构。

## 0.5 四要素 ↔ 实现映射（2026-06-27 现状）

### 0.5.1 人格（Personality）⭐⭐⭐⭐⭐ 最强

| 实现层 | 模块/字段 | 状态 |
|--------|----------|------|
| 数据 | `Character.personality` (JSON) | ✅ |
| 数据 | `Character.speaking_style` / `values` / `habits` / `long_term_goal` | ✅ |
| 情绪引擎 | `backend/jiwen/jiwen_core.py` (5 轴数学漂移) | ✅ |
| 情绪管理 | `backend/jiwen/jiwen_manager.py` (单例 + DB 持久化) | ✅ |
| 情绪调度 | `backend/jiwen/jiwen_scheduler.py` (asyncio 后台 tick) | ✅ |
| REST API | `jiwen_router.py` (17 个端点) | ✅ |
| 感知 | `DirectorModule.analyze_with_fallback()` (L=1 注意力聚焦) | ✅ |
| 表达 | `ActorModule.generate_stream()` (L=2 行为/语言生成) | ✅ |
| 双 LLM 管路 | `InteractionPipeline.run_stream()` (Temperature 0.5 / 0.8) | ✅ |
| 生长 | `growth.py` (角色成长推演) | ✅ |
| 提示词 | `prompts/director.txt` / `prompts/actor.txt` / `prompts/creation.txt` | ✅ |
| 单元测试 | `test_jiwen_core.py` / `test_jiwen_integration.py` (60 用例) | ✅ |

**评价**：5 星。jiwen 移植 + Director/Actor 双 LLM + 5 轴情绪已构成完整人格体系。

### 0.5.2 时间（Time）⭐⭐⭐⭐ 强

| 实现层 | 模块/字段 | 状态 |
|--------|----------|------|
| 数据 | `Character.day_number` (Int, 默认 1) | ✅ |
| 时间引擎 | `backend/modules/time.py` `TimeEngine` (日程生成 + 迭代) | ✅ |
| REST API | `event_router.py` (时间推进 / 事件触发) | ✅ |
| 提示词 | `prompts/time.txt` (LLM 生成日程) | ✅ |
| 时间感知 | `TimeEngine.iterate(character_id)` 按天数推进 | ✅ |

**评价**：4 星。**单一角色视角**的时间推进完善（**TimeEngine** + `day_number`），但**多角色时间同步**、**季节/天气**、**节日/纪念日** 等世界级时间维度未建模。

### 0.5.3 记忆（Memory）⭐⭐⭐⭐ 强

| 实现层 | 模块/字段 | 状态 |
|--------|----------|------|
| 长期记忆 | `Memory` 表 (含 strength/recall_count/forgotten/decay_rate) | ✅ |
| 记忆摘要 | `MemorySummary` 表 (L2 滚动摘要) | ✅ |
| 5 分区提取 | `memory_extractor.py` (identity/music/taste/moment/todo) | ✅ |
| 衰减算法 | `memory_decay.py` (Ebbinghaus 曲线 + importance 修正) | ✅ |
| 摘要触发 | `summary_trigger.py` (自适应 forgotten_ratio > 0.3) | ✅ |
| 情绪持久化 | `JiwenState` / `JiwenTrigger` 表 | ✅ |
| post_chat 钩子 | `post_chat.py` (applyDelta→extract→decay→summary) | ✅ |
| L1/L2/L3 架构 | 全量留存 + 滚动摘要 + ChromaDB 向量检索 | ✅ |
| REST API | `memory_router.py` (5 个端点) | ✅ |
| 单元测试 | `test_memory_decay.py` / `test_jiwen_integration.py` (57 用例) | ✅ |

**评价**：4.5 星。三层遗忘架构 + Ebbinghaus 衰减 + 自适应摘要已形成生产级方案。**弱点**：RAG 检索未做 query 改写 / 关键词与向量结果融合权重未调优。

### 0.5.4 世界（World）⭐⭐ 弱 ⚠️

| 实现层 | 模块/字段 | 状态 |
|--------|----------|------|
| 世界设定（静态） | `Character.world_setting` (Text，LLM 生成) | ⚠️ 文本无结构 |
| 当前状态（动态） | `Character.current_state` (JSON，含 `location` 字段) | ⚠️ 字段无约束 |
| 事件表 | `Event` 表 (用于 `TimeEngine` 推进) | ✅ |
| 事件引擎 | `event.py` `EventManager.advance_one()` | ✅ |
| 地点表 | ❌ `Location` 表**缺失** | ❌ |
| 物品/道具表 | ❌ `Item` / `Object` 表**缺失** | ❌ |
| 世界规则 | ❌ 世界级 state machine**缺失** | ❌ |
| 多 NPC 关系网 | ❌ `Relationship` 表**缺失**（目前 N 个角色互相不感知） | ❌ |
| 跨角色事件 | ❌ 事件只能由 `TimeEngine` 单角色推进 | ❌ |
| 季节/天气/地理 | ❌ 全局世界日历**缺失** | ❌ |

**评价**：2 星。**世界是四要素中最弱的一环**。当前仅靠 `world_setting`（背景文本）+ `current_state.location`（当前位置字符串）+ `Event`（孤立事件）拼凑，**没有显式的世界模型**。

> 详见 §0.6 差距分析与 §10 待办。

## 0.6 愿景 ↔ 实现差距分析（2026-06-27）

| 维度 | 愿景要求 | 当前实现 | 差距 |
|------|---------|---------|------|
| **持续存在** | 关闭浏览器后 NPC 仍在后台思考/感知世界 | jiwen 后台 tick ✅ + TimeEngine 按天推进 ✅ | 基本满足 |
| **自主成长** | NPC 能根据经历自动成长，不依赖用户指令 | `growth.py` 推演 + jiwen 5 轴漂移 | ✅ 已实现 |
| **长期演化** | 跨月/跨年保持人格连贯性 | 记忆系统 + 情绪持久化 | ✅ 已实现 |
| **人格** | 性格驱动所有行为 | 双 LLM 管路 + jiwen 5 轴 | ⭐⭐⭐⭐⭐ |
| **世界** | 角色"在世界里"，受世界约束 | 仅文本背景 + 单点 location | ⭐⭐ **需重大投入** |
| **时间** | NPC 知道"今天"和"今年" | `day_number` + `TimeEngine` | ⭐⭐⭐⭐ |
| **记忆** | 选择性记忆 + 跨时间检索 | L1/L2/L3 + Ebbinghaus + RAG | ⭐⭐⭐⭐ |

**核心结论**：
1. **人格/时间/记忆**三要素已构成生产级 NPC 系统
2. **世界**要素处于**PoC 阶段**，是 2026-07+ 重点投入方向
3. 当前项目可演示"有记忆、有情绪、会主动找你"的单角色 NPC，但**演示不出"NPC 在世界里"**
4. ADR-009 计划 v0.4 起建立 `Location` / `Item` / `Relationship` 三张表 + `WorldEngine` 模块

## 0.7 文档分层导航

| 层级 | 章节 | 关注者 | 文档形态 |
|------|------|--------|---------|
| **愿景层（WHY）** | §0 创始人原始愿景 + 0.5 四要素映射 + 0.6 差距 | 发起人/PM | 一句话 + 表格 |
| **架构层（WHAT）** | §1 顶层架构 + §6 数据流 | 新加入工程师 | 拓扑图 + 时序图 |
| **设计层（HOW）** | §7 ADR + §8 硬约束 | 重构者/Code Review | ADR 表格 + 约束清单 |
| **实施层（WHEN）** | §9 测试 + §10 待办 + §11 启动速查 | 实施者 | 测试矩阵 + 启动命令 |
| **生态层（TOOLS）** | §13 分析工具选型 | 工具选型者 | 工具对比表 |

---

## 1. 顶层架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Browser (React 18 SPA)                       │
│  web/react-vite/src                                                  │
│   ├─ pages/      (9 路由，React.lazy 按 page 拆 chunk)                │
│   ├─ components/ (15 组件：ChatBubble / Radar / Modal / ...)          │
│   ├─ hooks/      (5 业务 hook + useToast / useKeyboard)              │
│   ├─ router/     (BrowserRouter + Routes + Outlet Layout)            │
│   └─ utils/                                                              │
│        ├─ ApiContext        ← 切 realApi / mockApi                    │
│        ├─ CharactersContext ← 跨页共享角色列表                          │
│        ├─ api.js  (mockApi)  ← 离线 in-memory 模拟                    │
│        └─ realApi.js (realApi) ← 真实后端 HTTP 客户端                   │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ HTTP /api/* (CORS 5173-5175 + 8000)
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  FastAPI app  (backend/main.py — 286 行)            │
│  职责：app 装配 / CORS / 启动事件 / 异常兜底 / 静态文件                  │
└────┬──────────┬──────────┬──────────┬──────────┬──────────┬────────┘
     ▼          ▼          ▼          ▼          ▼          ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────┐
│ character│ │  chat   │ │ session │ │ growth  │ │  event  │ │ jiwen│  ...
│ _router  │ │ _router │ │ _router │ │ _router │ │ _router │ │router│
└────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └──┬───┘
     │           │           │           │           │         │
     └───────────┴───────────┴─────┬─────┴───────────┴─────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    业务编排层 (backend/modules/)                       │
│  ├─ interaction.py        InteractionPipeline (Director+Actor)        │
│  ├─ creation.py           CreationModule (角色生成)                    │
│  ├─ growth.py             GrowthModule (成长推演)                      │
│  ├─ event.py              EventManager (事件推进)                      │
│  ├─ time.py               TimeEngine (时间迭代)                        │
│  ├─ post_chat.py          聊天后钩子 (delta→extract→decay→summary)     │
│  ├─ memory_extractor.py   LLM 提取 5 分区记忆                          │
│  ├─ memory_decay.py       Ebbinghaus 衰减                              │
│  └─ summary_trigger.py    自适应摘要触发                                │
└──────────────────────────────────┬──────────────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    情绪引擎层 (backend/jiwen/)                        │
│  ├─ jiwen_core.py        5 轴漂移算法 (Node.js → Python 1:1 移植)     │
│  ├─ jiwen_manager.py     per-character 单例 + DB 持久化               │
│  └─ jiwen_scheduler.py   asyncio 后台 tick (5min interval)           │
└──────────────────────────────────┬──────────────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    持久化层                                           │
│  ├─ services/llm_service.py   LLMService 单例 (热更新)                │
│  ├─ services/llm_settings_store.py  settings.json 读写                │
│  ├─ services/logging_service.py    后台 worker 异步写日志              │
│  ├─ services/db_migration.py       幂等 schema 迁移                   │
│  ├─ services/chat_session_crud.py  ChatSession 专用 DAO                │
│  ├─ crud/                          通用 DAO (5 个)                    │
│  ├─ memory/                        LongTerm/ShortTerm/KB/ContextMgr  │
│  └─ models.py                      10 张表 (characters/...)          │
└──────────────────────────────────┬──────────────────────────────────┘
                                   ▼
                   ┌──────────────────────────────────┐
                   │  SQLite: usercontext/             │
                   │  characterseed.db (单文件)        │
                   │  llm_settings.json                │
                   │  logs/YYYY-MM-DD.jsonl            │
                   └──────────────────────────────────┘
                                   │
                                   ▼ HTTPS (httpx, keepalive=0)
                   ┌──────────────────────────────────┐
                   │  LLM Providers (5 个)             │
                   │  Qwen / Agnes / OpenAI /          │
                   │  Anthropic / Mock                  │
                   └──────────────────────────────────┘
```

***

## 2. 技术栈清单

| 层        | 技术           | 版本     | 角色                            |
| -------- | ------------ | ------ | ----------------------------- |
| 后端语言     | Python       | 3.13   | —                             |
| Web 框架   | FastAPI      | latest | REST + SSE 流式                 |
| ORM      | SQLAlchemy   | 2.x    | declarative\_base             |
| 校验       | Pydantic     | v2     | schemas / request body        |
| LLM 客户端  | openai SDK   | latest | 兼容所有 provider                 |
| HTTP 客户端 | httpx        | latest | 显式 keepalive 控制               |
| 前端框架     | React        | 18     | 函数组件 + Hooks                  |
| 构建       | Vite         | latest | 懒加载分包 + manualChunks          |
| 路由       | react-router | v6     | BrowserRouter + Outlet Layout |
| 图表       | recharts     | latest | 雷达图 / 折线图                     |
| 图标       | lucide-react | latest | —                             |
| 数据库      | SQLite       | 3      | 单文件本地                         |
| 异步       | asyncio      | stdlib | 后台 tick                       |
| 测试       | pytest       | latest | 87+ 测试                        |

***

## 3. 后端模块清单

### 3.1 router 层（11 个，main.py include\_router 注册）

| Router                    | 前缀                              | 主要职责                   | 状态                |
| ------------------------- | ------------------------------- | ---------------------- | ----------------- |
| `character_router`        | `/api/characters`               | 角色 CRUD + 描述润色         | ✅ 测试              |
| `chat_router`             | `/api/chat`                     | 对话（同步 + SSE 流式）        | ✅ 测试              |
| `session_router`          | `/api/sessions`                 | ChatSession 会话管理       | ✅ 测试              |
| `growth_router`           | `/api/growth`                   | 成长推演 + 状态更新            | ✅ 测试              |
| `event_router`            | `/api/events`                   | 事件推进 + 时间迭代            | ✅ 测试              |
| `character_memory_router` | `/api/characters/{id}/memories` | 角色记忆/对话/成长读路径          | ✅ 测试              |
| `performance_router`      | `/api/performance`              | 缓存统计 + 失效              | ✅ 测试              |
| `llm_router`              | `/api/llm`                      | LLM 设置 + 模型列表 + 连接测试   | ✅ 测试              |
| `logs_router`             | `/api/logs`                     | 日志查询 + 告警配置            | ✅ 测试              |
| `memory_router`           | `/api/memory`                   | 增强记忆系统（ContextManager） | ⚠️ 端点契约需独立 app 验证 |
| `jiwen_router`            | `/api/jiwen`                    | jiwen 17 端点（状态/触发器/调度） | ✅ 测试              |

### 3.2 modules 层（业务编排）

| 模块                        | 核心类                                                                                  | 行数级  | 单测 |
| ------------------------- | ------------------------------------------------------------------------------------ | ---- | -- |
| `interaction.py`          | `DirectorModule` / `ActorModule` / `InteractionPipeline` / `_IncrementalActorParser` | 730+ | ✅  |
| `creation.py`             | `CreationModule`                                                                     | —    | ✅  |
| `growth.py`               | `GrowthModule`                                                                       | —    | ✅  |
| `event.py`                | `EventManager`                                                                       | —    | ✅  |
| `time.py`                 | `TimeEngine`                                                                         | —    | ✅  |
| `post_chat.py`            | `post_chat_hooks` / `infer_emotion_delta`                                            | 200+ | ✅  |
| `memory_extractor.py`     | `extract_memories_from_conversation`                                                 | 240+ | ✅  |
| `memory_decay.py`         | `compute_current_strength` / `run_decay_pass`                                        | 210+ | ✅  |
| `summary_trigger.py`      | `should_summarize` / `create_summary`                                                | 290+ | ✅  |
| `enhanced_interaction.py` | 增强版管线                                                                                | —    | ⚠️ |

### 3.3 jiwen 层（独立子系统）

| 文件                   | 行数    | 职责                                              |
| -------------------- | ----- | ----------------------------------------------- |
| `jiwen_core.py`      | \~520 | 5 轴漂移 + 触发器（纯算法）                                |
| `jiwen_manager.py`   | \~450 | per-character 单例 + DB 持久化 + session\_factory 注入 |
| `jiwen_scheduler.py` | \~180 | asyncio 后台 tick                                 |

### 3.4 services 层

| 服务                      | 职责                               | 关键决策                                |
| ----------------------- | -------------------------------- | ----------------------------------- |
| `llm_service.py`        | LLM 调用 + 重试 + 客户端重建              | keepalive=0（Agnes 兼容）；read=30s（慢响应） |
| `llm_settings_store.py` | `llm_settings.json` 持久化 + 环境变量回退 | 空串不覆盖（PROVIDER\_DEFAULTS 兜底）        |
| `logging_service.py`    | 后台 worker 异步写日志 + 告警触发           | daemon Thread + 队列                  |
| `db_migration.py`       | 幂等 schema 迁移                     | `run_all_migrations`                |
| `chat_session_crud.py`  | ChatSession 专用 DAO               | 拆分自通用 crud                          |
| `notifiers.py`          | 告警通道（webhook/email/console）      | JSON 配置                             |
| `llm_api_tester.py`     | LLM 延迟测试                         | —                                   |

### 3.5 memory 层（增强记忆系统）

```
backend/memory/
├── context_manager.py     上下文窗口管理（token 预算 + 截断）
├── knowledge_base.py      知识库（事实/偏好/事件 三类）
├── long_term.py           L2 长期记忆
├── short_term.py          L1 短期记忆（会话内）
└── README.md              设计文档
```

⚠️ **警告**：`memory_router` 在 main.py 中**未 include\_router**（待修复）。端到端访问会被 React catch-all 截走，验证必须用独立 FastAPI app 隔离。

***

## 4. 前端结构

### 4.1 Provider 链

```
React.StrictMode
  └─ ErrorBoundary
       └─ ApiProvider          ← 切 realApi / mockApi
            └─ AppRouter (BrowserRouter)
                 └─ App (Layout: NavBar + Outlet)
                      └─ CharactersProvider  ← 跨页共享角色列表
                           └─ <Outlet />  渲染当前 page
```

### 4.2 路由表（9 + 1 fallback）

| 路径          | Page         | 显示 | 用途                |
| ----------- | ------------ | -- | ----------------- |
| `/chat`     | ChatPage     | ✓  | 对话（核心入口）          |
| `/create`   | CreatePage   | ✓  | 创建角色              |
| `/status`   | StatusPage   | ✓  | 角色状态（jiwen 5 轴雷达） |
| `/events`   | EventsPage   | ✓  | 事件时间线             |
| `/memory`   | MemoryPage   | ✓  | 记忆/对话/成长查看        |
| `/settings` | SettingsPage | ✓  | LLM 设置            |
| `/growth`   | GrowthPage   | ✓  | 成长控制              |
| `/logs`     | LogsPage     | ✓  | 系统日志              |
| `*`         | NotFoundPage | —  | 404               |

### 4.3 业务 Hook

| Hook              | 职责              | 关键点                            |
| ----------------- | --------------- | ------------------------------ |
| `useCharacters`   | 跨页共享角色列表        | mock→real 切换时 stale id 处理      |
| `useSessions`     | 会话管理 + 消息管理     | `patchSession` 浅比较避免 re-render |
| `useToast`        | 全局通知            | —                              |
| `useKeyboard`     | 快捷键             | Cmd+K 调起 CommandPalette        |
| `useLocalStorage` | localStorage 包装 | 类型安全                           |

### 4.4 API 模式切换（3 优先级）

```
URL ?api=real/mock  >  localStorage 'cs.apiMode.v1'  >  默认 mock
```

切换方式：

1. URL 参数（`http://host:5173/?api=real`）
2. localStorage（`window.localStorage.setItem('cs.apiMode.v1', 'real')` + 刷新）
3. DevTools（`window.__setApiMode('real')` 热切换，不刷新）

### 4.5 主题系统（4 套）

`light` / `dark` / `warm` / `contrast`，通过 `data-theme` + `data-style` 双 attribute 切换，CSS 用 `color-mix(in srgb, var(--accent) X%, transparent)` 派生色值，不硬编码 `rgba(99,102,241,...)`。

***

## 5. 数据库 Schema（10 表）

```
┌──────────────┐
│  characters  │ 主表
└──────┬───────┘
       │ 1:N
       ├──────────────┐
       │              │
       ▼              ▼
┌──────────────┐  ┌──────────────┐
│ chat_sessions│  │   memories   │  L3 记忆碎片（增强）
└──────┬───────┘  │  - theme     │  identity/music/taste/moment/todo
       │ 1:N      │  - strength  │  0-10 衰减函数维护
       ▼          │  - recall_count
┌──────────────┐  │  - forgotten │  0/1 软删除
│conversations │  │  - decay_rate│  0-100 per-day 基线
└──────────────┘  └──────────────┘
       │
       ├──────────────┐
       │              │
       ▼              ▼
┌──────────────┐  ┌────────────────┐
│  events      │  │ memory_summaries│  L2 摘要（chain supersede）
│  (timeline)  │  │  - superseded_by│
└──────────────┘  └────────────────┘

┌──────────────┐
│ jiwen_states │  per-character 5 轴 + 累计统计
└──────────────┘

┌──────────────┐
│jiwen_triggers│  触发器落地（observation/contact/find_activity）
└──────────────┘

┌──────────────┐
│ growth_logs  │  成长推演日志
└──────────────┘

┌──────────────┐
│  error_logs  │  严重错误实时落地（CRITICAL/ERROR）
└──────────────┘

┌──────────────┐
│ alert_config │  告警配置（单条 id=1，channels=JSON 数组）
└──────────────┘
```

复合索引（高频查询优化）：

- `ix_chat_sessions_char_updated` (character\_id, updated\_at DESC)
- `ix_conversations_session_timestamp` (session\_id, timestamp)
- `ix_memories_char_active_strength` (character\_id, forgotten, strength)
- `ix_memories_char_theme` (character\_id, theme)
- `ix_memory_summaries_char_active` (character\_id, is\_active)
- `ix_events_char_day_order` (character\_id, day\_number, order\_index)
- `ix_events_char_status` (character\_id, status)
- `ix_error_logs_level_time` (level, created\_at)
- `ix_error_logs_type_time` (error\_type, created\_at)

***

## 6. 关键数据流

### 6.1 用户聊天 → LLM 回复

```
[用户键入] ChatPage
   │ POST /api/chat/send
   ▼
chat_router.send_message
   │ 1) 拿角色 + 历史 messages + jiwen 状态
   │ 2) 命中缓存?  cache_key = "{cid}:{input_hash16}:b{history_bucket}"
   │ 3) 未命中 → InteractionPipeline.run_stream
   │       │
   │       ├─ Director.analyze_with_fallback  (T=0.5, json_object)
   │       │     输入：personality / current_state（含 _jiwen 子字段）/
   │       │          recent_memories / user_input / history_messages
   │       │     输出：emotion / focus_memories / goal / style
   │       │
   │       ├─ Actor.generate_stream  (T=0.8, json_object, SSE)
   │       │     输入：emotion / focus_memories / goal /
   │       │          style（追加 jiwen.get_style_guidance()）
   │       │     输出：action / expression / speech（流式 speech_delta）
   │       │
   │       └─ 写 LRU 缓存（TTL 5min, MAX 512） + 持久化 Conversation
   ▼
[SSE 事件流] onChunk → speech_delta 累加渲染
[done 事件]   ChatBubble 提交
[onMeta]      session_title 同步 → useSessions.patchSession
[post_chat 钩子] background:
   ├─ 1) jiwen.apply_delta
   ├─ 2) memory_extractor.extract_and_save
   ├─ 3) memory_decay.run_decay_pass
   └─ 4) summary_trigger.maybe
```

### 6.2 jiwen 后台 tick（每 5 分钟）

```
JiwenScheduler (asyncio)
   │ tick_now / interval 5min
   ▼
JiwenManager.tick_all_active
   │ for each character:
   ▼
JiwenEngine.tick(minutes)
   │ 计算 5 轴漂移（向 0 回归）
   │ 检查三档触发器：
   │   - observation:  connection >= 0.20
   │   - contact:      connection >= 0.35
   │   - find_activity: valence <= valenceActivity  OR  arousal >= arousalAgitation
   ▼
JiwenTrigger 落库 (jiwen_triggers 表)
   │
   └─ contact 触发 → 前端 EventsPage 12s 轮询 + visibilitychange 立即 refresh
```

### 6.3 设置页改 LLM provider → 热更新

```
SettingsPage 保存
   │ PUT /api/llm/settings
   ▼
llm_router → 写入 llm_settings.json
   │
   ├─> LLMService._instance.reload_config()
   │     → 重建 OpenAI client（按 provider 选 keepalive limits）
   │
   └─> state.reload_all_llm()
         ├─ 遍历所有持 .reload() 的单例（creation/pipeline/growth/event/time）
         ├─ cache_invalidate()  ← 清响应缓存
         └─ char_data_cache_invalidate()  ← 清角色数据缓存
```

### 6.4 记忆三层系统（L1 / L2 / L3）

```
聊天 → post_chat_hooks
   │
   ├─ L3 记忆碎片 (memories 表)
   │   提取 → importance 0-10 → theme 5 分区 → strength 0-10
   │   衰减：Ebbinghaus 7~91 天半衰期 + recall boost
   │   软删除：forgotten=1（从检索池过滤，但保留）
   │
   ├─ 自适应摘要触发
   │   下限 20 条 / 上限 100 条 / 中间 forgotten_ratio > 0.3
   │
   └─ L2 滚动摘要 (memory_summaries 表)
       链式：superseded_by 指向新摘要
       永不删（旧摘要留底），superseded 后从检索池过滤
   │
   └─ L1 永不删 (conversations 表)
```

***

## 7. 关键设计决策（ADR）

### ADR-001：jiwen 用纯算法移植，**不**走 HTTP sidecar

**背景**：原 jiwen 是 Node.js 库，CharacterSeed 是 Python FastAPI。
**选项**：

- A) 跑 Node.js sidecar，HTTP 调用
- B) Python 纯算法移植

**决策**：B。
**理由**：

- jiwen 是纯数学漂移（5 轴 + 触发器），无 Node 特有 API
- HTTP sidecar 每次 tick 多 5-20ms 延迟 + IPC 复杂度
- 本地数据 + 5min tick 频率，零跨进程收益巨大
- 移植成本 1 天，维护成本长期更低

### ADR-002：Director + Actor 双 LLM 管路

**背景**：单 LLM 同时做"想"和"说"会失衡（要么太理智要么太感性）。
**决策**：拆为 Director（T=0.5，思考）+ Actor（T=0.8，表达）。
**理由**：

- 可解释性：Director 的 emotion / focus\_memories / goal 可独立可视化
- 可调试性：两个 prompt 独立调优
- 鲁棒性：每节点独立 try/except + 降级输出（`FALLBACK_DIRECTOR_OUTPUT` / `FALLBACK_ACTOR_OUTPUT`）

### ADR-003：响应缓存 + 角色数据缓存

**决策**：

- 响应缓存：5min TTL + LRU(512)，key = `{cid}:{input_hash16}:b{history_bucket}`（分桶避免多轮误命中）
- 角色数据缓存：60s TTL + LRU(256)，缓存 personality/current\_state 解析后的 dict

**理由**：

- 响应缓存：用户重试 / 短时间重复提问命中 → 跳过两次 LLM 调用
- 角色数据缓存：每次 run 都解析 JSON，缓存减少 \~0.1-0.5ms/次

**失效**：

- 响应缓存：reload\_all\_llm 主动清 + LRU 淘汰 + 过期
- 角色数据缓存：角色 CRUD 后主动清 + LRU 淘汰 + 过期

### ADR-004：LLM settings 持久化到 JSON 文件（不入 DB）

**决策**：`usercontext/llm_settings.json`，不入 SQLite。
**理由**：

- LLM settings 改动频繁，DB 事务开销不必要
- JSON 文件可直接手动编辑，调试友好
- 配置：API key / base\_url / model / provider 切换
- **空串不覆盖** + `PROVIDER_DEFAULTS` 兜底（避免空字符串把旧值擦掉）

### ADR-005：jiwen 状态注入 LLM 不改 prompt 模板

**决策**：把 5 轴状态塞进 `current_state._jiwen` 子字段（Director prompt 可见），把 `get_style_guidance()` 追加到 Actor 的 `style` 字符串。
**理由**：

- 零模板侵入，零迁移成本
- 现有 prompt 已支持 `current_state` JSON，自动包含 `_jiwen` 子字段
- 测试可通过 `monkeypatch.setattr` FakeDirector 捕获 `analyze_with_fallback` 入参验证

### ADR-006：background 线程的 DB 操作必须自管 session

**决策**：`with SessionLocal() as db`，不走 `Depends(get_db)`。
**理由**：

- `Depends(get_db)` 是 request scope，scheduler tick 来自后台线程
- 后台线程必须自管 session 生命周期
- 测试隔离：post\_chat / jiwen\_manager 必须支持 `session_factory` 参数注入 TestingSessionLocal

### ADR-007：jiwen 5 轴范围与原 JS 版保持 1:1

**决策**：connection/immersion 0→1；pride/valence/arousal -1→+1。
**理由**：

- 触发器阈值（observation=0.20 / contact=0.35）依赖原始范围
- 缩放到 0\~100 会破坏触发器行为
- DB 存储用 0-100 整数（精度损失 < 1%），但 API 端仍返回 0\~1 浮点

### ADR-008：代码分析工具用 codebase-memory-mcp

详见 §13。**accepted**（2026-06-27 实测通过 3,129 节点索引；CLI `index_repository` bug 用 stdio JSON-RPC 绕过；3D UI 因 Trae IDE 不在支持列表降级为手动分析模式）。

### ADR-009：v0.4 起建立"世界"四要素的显式数据模型

**背景**：
- 创始人原始愿景 = 四要素（人格/世界/时间/记忆）**并列**
- 当前实现：**人格 5 星 / 时间 4 星 / 记忆 4 星 / 世界 2 星**（详见 §0.5.4）
- "世界" 是 4 要素中最薄弱的一环，没有 `Location` / `Item` / `Relationship` 等结构化表

**决策**：
- v0.4（2026-Q3）起新增以下 3 张表 + 1 个引擎：

| 新增 | 职责 | 关键字段 |
|------|------|---------|
| `Location` | 地点（地图节点） | id, name, parent_id (嵌套), world_id, climate, description |
| `Item` | 物品/道具 | id, name, owner_id (人或 Location), properties (JSON) |
| `Relationship` | 多 NPC 关系网 | id, char_a_id, char_b_id, type, strength, history (JSON) |
| `WorldEngine` | 世界级时间推进 | tick_world(day) 推进季节/天气/全局事件；broadcast 给所有角色 |

**正向**：
- 让"NPC 在世界里"愿景真正落地
- 多 NPC 关系网支持群像故事线
- 跨角色事件可广播（生日会、世界级节日）

**反向**：
- 3 张新表 → schema 迁移复杂
- WorldEngine 后台 tick → 增加 CPU/IO
- LLM 提示词需扩展（"你看到窗外下雨"等世界感知）

**可逆**：中。新表可加列不破坏老逻辑，但 WorldEngine 重构牵涉 event/time 模块。

**状态**：proposed（2026-07 计划）

### ADR-010：项目愿景回归"游戏 NPC"语境，弱化"陪伴"重定位

**背景**：
- 2026-06 文档 v0.1 曾把项目重定位为"AI 角色**陪伴**系统"
- 创始人原始表述（README 与设计文档）是"**基于人格、世界、时间和记忆驱动的 NPC 生命系统**，为游戏角色提供持续存在、自主成长和长期演化能力"
- 陪伴 vs NPC：前者"用户为主、NPC 为工具"；后者"NPC 为主、用户为共在者"

**决策**：
- §0 全面回归创始人原始表述
- "单机版/有生命感/AI 角色" → 调整为 "NPC 生命系统 / 游戏角色 / 持续存在/自主成长/长期演化"
- 文档分层 §0.7 明确 WHY = §0，HOW = §1-9

**理由**：
- 创始人原始表述更清晰强调"四要素并列"的产品定位
- 避免与市面 AI 陪伴 App 同质化
- "游戏 NPC" 定位更利于未来接入真实游戏引擎（Unity/Unreal）

**状态**：accepted（2026-06-27）

***

## 8. 关键硬约束（详见 project\_memory.md）

| 类别      | 约束                                                           |
| ------- | ------------------------------------------------------------ |
| LLM 配置  | `update_provider` 空串不覆盖；masked 串（`sk-12****5678`）不入后端        |
| LLM 客户端 | Agnes keepalive=0；其他 provider keepalive=5；read=30s           |
| 持久化     | background 线程用 `SessionLocal` 或注入的 session\_factory          |
| 前端      | `?api=real` 直连 8000 端口（绕开 Vite 代理 SSE 背压）                    |
| 前端      | 4 套主题禁止 hardcode `rgba(99,102,241,...)`                      |
| 前端      | `import { memo as ReactMemo }` 只在一个文件 import                 |
| 单例      | jiwen\_manager / post\_chat 必须支持 session\_factory 注入         |
| Router  | jiwen\_router 必须 `app.include_router(...)` 注册（漏注册 9 端点全 405） |
| Router  | DB 操作必须 `Depends(get_db)`，不能用 `with SessionLocal() as db`    |
| SQLite  | 复合索引 + foreign\_keys=ON（外键默认禁用）                              |

***

## 9. 测试策略

| 测试文件                              | 数量       | 覆盖                                     |
| --------------------------------- | -------- | -------------------------------------- |
| `test_jiwen_core.py`              | 30       | 5 轴漂移 / 触发器阈值 / drift 算法               |
| `test_jiwen_integration.py`       | 30       | jiwen 集成 + 17 个 REST API + Pipeline 注入 |
| `test_memory_decay.py`            | 27       | Ebbinghaus 衰减 + soft-deprecate         |
| `test_character_router.py`        | —        | 角色 CRUD + 描述润色 + 级联删除                  |
| `test_chat_router.py`             | —        | 同步 + 流式 + 缓存命中                         |
| `test_session_router.py`          | —        | ChatSession 级联 conversation            |
| `test_llm_router.py`              | —        | LLM 设置 + 模型列表 + 连接测试                   |
| `test_event_router.py`            | —        | 事件推进 + 时间迭代                            |
| `test_growth_router.py`           | —        | 成长推演 + 响应缓存失效                          |
| `test_logs_router.py`             | —        | 日志查询 + 告警配置                            |
| `test_performance_router.py`      | —        | 缓存统计                                   |
| `test_character_memory_router.py` | —        | 角色记忆读路径                                |
| `test_enhanced_pipeline.py`       | —        | 增强管线                                   |
| `test_memory_router.py`           | —        | 增强记忆系统（独立 app 验证）                      |
| **合计**                            | **200+** | —                                      |

**测试隔离三件套**（conftest.py `_isolate_test_state` fixture）：

```python
# 1) 路由层
app.dependency_overrides[get_db] = _override_get_db

# 2) 单例层
JiwenManager._instance = JiwenManager(session_factory=TestingSessionLocal)
monkeypatch.setattr(jm_module, "get_jiwen_manager", lambda: fresh)

# 3) 模块层引用
monkeypatch.setattr(post_chat_module, "SessionLocal", TestingSessionLocal)
```

少任何一件都会留下 stale singleton / 跨 DB 读。

***

## 10. 已知问题与待办

### P0（堵愿景，必须修）

- [ ] **【世界四要素】** v0.4 起建立 `Location` / `Item` / `Relationship` 三张表 + `WorldEngine`（ADR-009）—— 详见 §0.5.4 / §0.6
- [ ] **【世界四要素】** `Character.world_setting` 改为结构化（关联 `Location` 表 / `WorldRule` 表）
- [ ] **【世界四要素】** `Character.current_state.location` 从字符串改为 `location_id` 外键

### P1（应修）

- [ ] `memory_router` 在 main.py **未 include\_router**（待修复；目前需独立 app 验证）
- [ ] `enhanced_interaction.py` 测试覆盖不足
- [ ] 前后端 id 映射仍存在硬编码（`char-2` ↔ 整数 id）——已有 `_toBackendCharId` helper，但 chat 流仍有遗漏

### P2（可做）

- [ ] ChromaDB 接入（metadata 字段已就绪，差 vector index 写入）
- [ ] 跨设备 jiwen 状态同步
- [ ] 摘要生成 LLM prompt 优化（当前 fallback 较 bland）
- [ ] OpenTelemetry 接入（已有 LoggingService 基础设施）
- [ ] **【世界四要素】** 多 NPC 群像故事线（依赖 Relationship 表）
- [ ] **【世界四要素】** 季节/天气/地理系统（依赖 WorldEngine）

***

## 11. 启动 & 调试速查

### 后端启动

```bash
cd CharacterSeed
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### 前端启动

```bash
cd CharacterSeed/web/react-vite
npm run dev
# 浏览器打开 http://localhost:5173
```

### 切真实后端

```
http://localhost:5173/?api=real
```

### 测试

```bash
cd CharacterSeed
$env:PYTHONPATH = "C:\Users\biren\Documents\trae_projects\luyan\CharacterSeed"
python -m pytest tests/test_jiwen_integration.py -v
```

### 日志位置

- 文件：`usercontext/logs/YYYY-MM-DD.jsonl`（普通 INFO/WARNING）
- DB：`usercontext/characterseed.db` 的 `error_logs` 表（ERROR/CRITICAL）
- 告警：channels 配置（console / webhook / email）

***

## 12. 文档导航

| 文档                                                                                                                     | 用途                     |
| ---------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| [architecture.md](./architecture.md)                                                                                   | 本文：整体架构                |
| [superpowers/plans/2026-06-27-jiwen-integration-design.md](./superpowers/plans/2026-06-27-jiwen-integration-design.md) | jiwen 详细设计（5 轴算法 + 决策） |
| [superpowers/plans/2026-06-26-frontend-audit-report.md](./superpowers/plans/2026-06-26-frontend-audit-report.md)       | 前端架构体检                 |
| [superpowers/plans/2026-06-26-frontend-refactor-suite.md](./superpowers/plans/2026-06-26-frontend-refactor-suite.md)   | 前端重构方案                 |
| [memory/README.md](../backend/memory/README.md)                                                                        | 增强记忆系统设计               |
| project\_memory.md（用户级 memory）                                                                                         | 跨会话踩坑沉淀（**最权威**）       |

***

## 13. 代码分析工具选型（2026-06-27 评估）

### 13.1 评估背景

为了在不依赖 IDE 的情况下深度理解 CharacterSeed 全栈架构（Python FastAPI + React/JSX + SQLAlchemy + jiwen 移植模块），需要一款**对 Windows 原生友好**、**支持 Python/TS/JSX 全栈**、**有可视化与结构查询**能力的代码分析工具。本次从候选 6 款中选型。

### 13.2 候选工具对比

| 工具                                  | 形态                      | 语言覆盖                                       | 可视化                         | 实时同步                  | 安装门槛           | 成熟度                              | 适合场景        |
| ----------------------------------- | ----------------------- | ------------------------------------------ | --------------------------- | --------------------- | -------------- | -------------------------------- | ----------- |
| **codebase-memory-mcp** (DeusData)  | 单 static binary         | **158 语言** (含 Python/TS/JSX/PHP/Go/Rust 等) | **3D 图** (`localhost:9749`) | ❌ 需手动 rebuild         | **零依赖** (下载即用) | 863 commits，**arXiv 2603.27277** | ⭐ **首选**    |
| **codegraph** (colbymchenry)        | CLI + MCP server        | 多语言                                        | 2D 仪表盘                      | ✅ **file watcher 实时** | Node 18+       | **1.0 released**，509 commits     | 次选（实时）      |
| **Understand-Anything** (Egonex-AI) | Claude Code Plugin      | 多语言                                        | 交互式 Dashboard               | ❌ 手动 `/understand`    | 需 Claude Code  | 596 commits，v2.8                 | Claude 用户优选 |
| **code-review-graph** (tirth8205)   | Python pip              | Tree-sitter 多语言                            | 2D 关系图                      | ✅ 增量                  | pip install    | v2.3.6，493 commits               | 偏 PR review |
| **emerge** (glato)                  | Python pip              | 12 语言                                      | D3 force-graph              | ❌ 手动                  | **依赖冲突** (已尝试) | 337 commits，最后 2024-10           | 弃用          |
| **codegraph-rust** (Jakedismo)      | Rust binary + LM Studio | 多语言                                        | 需自配                         | ✅ 增量                  | Rust toolchain | 761 commits，0 tag                | 较新，未验证      |

### 13.3 选型决策：codebase-memory-mcp

**理由**：

1. **Windows 原生**：单一 static binary（amd64），无运行时依赖，与用户环境（Windows + PowerShell）完全兼容
2. **语言全覆盖**：158 语言 vendor 进 binary，**Python + TypeScript + JSX/TSX 全部命中**（覆盖 backend + web/react-vite 两端）
3. **Hybrid LSP 语义解析**：对 Python/TS/JS 有完整 LSP 语义类型解析，能解析跨模块 import、类继承、调用图（不止纯 AST）
4. **性能优势**：28M LOC 的 Linux kernel 仅需 3 分钟索引（CharacterSeed 约 3k LOC 可秒级）
5. **3D 可视化**：`localhost:9749` 内置 3D 力导向图，便于审视模块耦合度
6. **14 个 MCP 工具**：search / trace / architecture / impact-analysis / Cypher / dead-code / HTTP-link / ADR 等结构化查询
7. **学术背书**：arXiv 2603.27277《Codebase-Memory: Tree-Sitter-Based Knowledge Graphs for LLM Code Exploration》

**安装命令**（Windows PowerShell）：

```powershell
# 下载并审计
Invoke-WebRequest -Uri https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.ps1 -OutFile install.ps1
notepad install.ps1   # 建议先审
.\install.ps1 --ui    # 启用 3D 可视化
```

**典型使用流程**：

```
1. "Index this project"           → 构建知识图（首次 < 1 min）
2. 浏览器打开 localhost:9749      → 3D 浏览模块/类/函数节点
3. 询问 Claude / Codex / Cursor   → 通过 MCP tools 查询结构
```

### 13.4 备选：codegraph（实时同步场景）

若需要 **保存即同步**（file watcher 实时 rebuild），选 codegraph。代价是依赖 Node 18+、需要 `codegraph install` 注入 agent。CharacterSeed 增量改动频繁时优先。

### 13.5 不选

- **emerge**：实测 pip install 失败（descript-audiotools / gradio / numpy 冲突），且 2024-10 后无更新 → 弃用
- **codegraph-rust**：0 tag、未充分验证，但 LM Studio + Jina 嵌入可作 vector search 备选
- **Understand-Anything**：仅 Claude Code 用户用得上；本项目用 Trae IDE，**MCP 兼容工具优先级更高**

### 13.6 实测报告（2026-06-27 17:30）

**安装**：✅ 成功

- 路径：`C:\Users\biren\AppData\Local\Programs\codebase-memory-mcp\codebase-memory-mcp.exe`
- 版本：CLI 报 `0.8.1`，但 server JSON-RPC 握手报 `0.10.0`（双版本号）
- 安装方式：`--ui` 标志（启用 3D 可视化 binary）
- 校验：SHA256 校验通过
- Agent 配置：失败（Trae IDE 不在支持列表，符合预期）

**索引**：✅ 成功（需走 stdio JSON-RPC，**CLI 模式** **`index_repository`** **有 bug**）

- 项目大小：3,129 节点 / 10,830 边 / 13.2 MB SQLite
- 排除目录：20 个（`.git`、`.venv`、`__pycache__`、`node_modules` 等）
- 工作流：用 PowerShell 管道发送 `initialize + notifications/initialized + tools/call(index_repository)` 三条 JSON-RPC 到 stdin

**查询验证**（全部通过 stdio JSON-RPC）：

| 工具                               | 结果                                                                                                             |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `list_projects`                  | 返回 1 个项目，13.2 MB                                                                                               |
| `get_architecture`               | 22 个 layer（api/core/entry/internal）+ 10 个 hotspot（fan-in top: `add`=45, `create_jiwen`=33, `get_character`=28） |
| `search_graph`                   | `InteractionPipeline` (in\_degree=25) 是核心枢纽；`DirectorModule`/`ActorModule` 是双 LLM 入口                           |
| `trace_path` (post\_chat\_hooks) | 3 跳覆盖 `extract_and_save` → `run_decay_pass` → `create_summary` → `apply_delta` 完整记忆/衰减/摘要链                     |
| `get_graph_schema`               | 14 节点类型 + 19 边类型（其中 `Route=159` REST 端点、`HTTP_CALLS=52` 跨服务调用、`IMPORTS=287`、`INHERITS=13`）                     |

**3D UI 验证**：⚠️ 部分支持

- `config.json` 中 `ui_enabled: true`、`ui_port: 9749` 已持久化
- 单独启动二进制时 UI **不**自动开 — 必须有 MCP 客户端持续连接
- README 明确："The UI runs as a background thread alongside the MCP server — it's available whenever your agent is connected"
- **结论**：需要接 Claude Code / Codex / Cursor 等支持列表中的 agent 才有 3D 可视化；Trae IDE 不在列表中 → UI 在本项目**不**可独立使用

### 13.7 已知 CLI 模式 Bug（v0.8.1）

- `codebase-memory-mcp cli index_repository '<json>'` 始终返回 `"repo_path is required"`，即使 JSON 中正确传递 `repo_path` 字段
- 文档示例（`{'repo_path': '/path/to/repo'}`）在 PowerShell 下不工作
- **workaround**：必须用 stdio JSON-RPC 三步握手（`initialize` → `notifications/initialized` → `tools/call`）
- `search_graph` / `get_architecture` / `trace_path` 等其他工具的 CLI 模式正常工作
- 建议：等 v0.10.x+ 修复，或直接用 stdio 协议调用

### 13.8 工具选型 ADR

**ADR-008：代码分析工具用 codebase-memory-mcp**

| 维度     | 决策                                                                                                                                           |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **结论** | 首选 `codebase-memory-mcp`；备选 `codegraph`；弃用 `emerge`                                                                                          |
| **触发** | 2026-06-27 架构文档收尾时需要全栈静态分析                                                                                                                   |
| **正向** | 158 语言 vendor、3D 可视化、14 MCP 工具、零依赖 single binary、arXiv 论文                                                                                    |
| **反向** | 不支持 file watcher 实时同步（备选 codegraph 补足）                                                                                                       |
| **可逆** | 高 — 工具无侵入（仅写 `.codebase-memory/` 目录）                                                                                                         |
| **状态** | **accepted**（2026-06-27 实测通过：3,129 节点 / 10,830 边成功索引；CLI `index_repository` 已知 bug 用 stdio JSON-RPC work-around；3D UI 因 Trae IDE 不在支持列表暂不启用） |

***

