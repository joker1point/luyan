# CharacterSeed v1.0 — AI 数字生命模拟系统

> 基于人格、记忆、世界和时间驱动的 NPC 生命系统。不再是"会说话的聊天机器人"，而是拥有持续成长能力的数字生命。

---

## 项目简介

CharacterSeed 是一套 AI NPC 生命模拟系统，使虚拟角色能够在交互中持续成长、沉淀记忆、演化人格。

**核心创新点**：

| 特性 | 说明 |
|---|---|
| 🌱 **Creation System** | 一句话描述或 TXT 故事文件 → 生成完整角色（名称、世界设定、6 维人格、初始记忆） |
| 🎭 **Director + Actor 双 LLM 管线** | Director 负责"注意力聚焦"（情绪/目标/风格），Actor 负责"行为生成"（动作/表情/语言）——可解释、可调试 |
| 📅 **事件推进 + 时间引擎** | 每个事件由 LLM 推演生成 result_json；日迭代 = 成长分析 + 生成次日 schedule（3-5 事件） + 落库 + day_number+1 |
| 🧠 **人格演化与成长** | 对话经历驱动人格六维属性动态变化，所有成长记录可追溯 |
| 💾 **多维角色画像** | speaking_style / values / habits / long_term_goal / day_number 全部持久化 |
| 📊 **全功能 Web 前端** | Vue 3 + Vite + TypeScript 多页面：角色创建 → 对话交互 → 事件中心 → 状态面板 → 设置，一键启动 |

---

## 架构特点

| 维度 | 选型 | 说明 |
|---|---|---|
| **状态管理** | ❌ **无 Pinia**，自实现 composable + 模块级 ref 单例 | 全局状态通过 `useCharacters()` / `useChat()` / `useEvents()` 三个 composable + 模块级 `ref` 暴露"当前选中角色"等单例。无 store 抽象层 |
| **UI 库** | ❌ **无 Element Plus / Naive UI / Ant Design**，全部自实现 CSS | 用原生 CSS 变量 (`var(--bg-card)` / `var(--text)` 等) + scoped style 实现主题切换、深色模式、响应式。所有按钮、卡片、徽章、对话气泡、事件时间线均手写 |
| **路由** | vue-router 4 | 5 个页面：`/create` `/chat` `/events` `/status` `/settings` |
| **HTTP 客户端** | 原生 `fetch` 封装 | 拦截器统一处理 JSON / 错误 / SSE |
| **后端框架** | FastAPI（无 nested APIRouter 抽象） | `main.py` 全部 `@app.get/post` 直挂，单文件 27+ 个端点（按 path 分组注释） |
| **业务模块** | Module 模式 | `CreationModule` / `InteractionPipeline` / `GrowthModule` / `EventManager` / `TimeEngine` 全部为单例，LLM 配置热更新 |
| **数据库** | SQLite + SQLAlchemy 2.0 | 6 张表，迁移用纯 SQL（`db_migration.py`），幂等可重跑 |
| **LLM 抽象** | OpenAI 兼容协议 | 支持 DeepSeek / Agnes AI / Qwen / OpenAI / Ollama，运行时切换无需重启 |

### 前后端通信

```
Vue SPA (5173)  ────HTTP/SSE────▶  FastAPI (8000)
                                          │
                                          ├── 单例：_creation_module / _pipeline
                                          ├── 单例：_growth_module / _event_manager / _time_engine
                                          └── SQLite  (data/character_seed.db)
```

开发期走 Vite 代理（`web/vite.config.ts`），生产期前后端同源部署在 8000（FastAPI `StaticFiles` 托管 `web/dist`）。

---

## 产品截图

### 🌱 角色创建页
- 文本输入 / TXT 文件上传双模式
- Creation LLM 自动生成：名称、世界设定、6 维人格、初始记忆
- 结果卡片展示 + LLM 原始响应（可展开调试）

### 💬 对话交互页
- 角色选择器 + 聊天气泡界面
- Director → Actor 双管线实时生成 NPC 回复
- 每条回复附带：情绪标签、动作描述、表情
- 可展开查看 Director/Actor 原始 JSON 输出
- 支持触发角色成长 + 清空当前对话

### 📊 角色状态面板
- 人格 6 维进度条可视化
- 当前状态（位置/活动/心情）
- 记忆列表（按类型筛选：对话/事件/成长）
- 对话历史记录表
- 成长记录折叠面板（人格 delta + 新增记忆）

### 📅 事件中心（Day 4）
- 按 Day 分组的事件时间线（morning/afternoon/evening/night）
- 3 个核心操作：
  - **推进一个** → LLM 推演单个 pending 事件，生成回执
  - **迭代到下一天** → 成长分析 + 生成次日 schedule（3-5 事件）+ 落库
  - **一键推演** → 串联"先推完 pending → 再迭代"
- 完整 director_raw / actor_raw / growth_raw 可展开调试

### ⚙️ 设置面板
- 5 厂商一键切换（DeepSeek / Agnes AI / Qwen / OpenAI / Ollama）
- API Key 脱敏显示（保留首尾 4 字符）
- "测试连接" 按钮：填完 Key 立即验证，不用重启
- API 联通测试 Dashboard：models 列表 / 流式延迟 / 原始请求探针

---

## 技术栈

| 层级 | 技术 | 版本 |
|---|---|---|
| 后端框架 | FastAPI | 0.104.1 |
| 前端框架 | Vue 3 + Vite + TypeScript | 5.4.x |
| 状态管理 | 自实现 composable（**无 Pinia**） | — |
| UI 组件 | 自实现 CSS（**无 UI 库**） | — |
| ORM | SQLAlchemy | 2.0.23 |
| 数据库 | SQLite | — |
| LLM | OpenAI 兼容协议（Agnes AI / DeepSeek / Qwen / OpenAI / Ollama） | openai>=1.30.0 |
| Python | 3.11+ | — |
| Node | 18+ | — |

---

## 环境配置

### 1. 前置要求

- Python 3.11 或更高版本
- DeepSeek API Key（在 [platform.deepseek.com](https://platform.deepseek.com) 获取）

### 2. 安装依赖

```bash
# 进入项目目录
cd CharacterSeed

# 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# 安装依赖
pip install -r requirements.txt
```

> 若使用 uv 管理依赖，可执行 `uv sync`。

### 3. 配置 API Key

```bash
# 复制环境变量模板
copy .env.example .env         # Windows
# cp .env.example .env         # macOS / Linux

# 编辑 .env 文件，填入你选定的 provider 的 API Key
# 5 选 1：DEEPSEEK / AGNES / QWEN / OPENAI / OLLAMA
# AGNES_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

.env 文件内容示例：

```
# 主用 provider（运行时可在设置页切换，无需重启）
AGNES_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AGNES_BASE_URL=https://apihub.agnes-ai.com/v1
AGNES_MODEL=agnes-1.5-flash

# 备选 provider（用于热切换对比）
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

DATABASE_URL=sqlite:///./data/character_seed.db
DEBUG=True
API_V1_STR=/api
```

---

## 快速启动

### 方式一：一键启动（推荐）

双击运行 `start_demo.bat`，脚本将自动：
1. 检查 Python 环境
2. 启动 FastAPI 后端（端口 8000）
3. 启动 Vue Web 前端（端口 5173）
4. 自动打开浏览器

关闭弹出的终端窗口即可停止全部服务。

### 方式二：手动分步启动

```bash
# 终端 1：启动后端
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 终端 2：启动前端
cd web && npm run dev
```

访问以下地址：

| 服务 | 地址 |
|---|---|
| 前端页面 | http://localhost:5173 |
| 后端 API 文档 (Swagger) | http://localhost:8000/docs |
| 后端健康检查 | http://localhost:8000/ |

---

## API 端点

启动后端后访问 http://localhost:8000/docs 可查看交互式 API 文档。

后端共 **25 个 `/api/*` 端点** + 1 个根路径 + 1 个 SPA fallback，按业务分组如下。

### 角色管理（4 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/characters/create` | 创建角色（文本描述或 TXT 文件上传） |
| `GET` | `/api/characters` | 获取角色列表（分页：skip / limit） |
| `GET` | `/api/characters/{id}` | 获取角色详情（含人格、状态、day_number、画像） |
| `DELETE` | `/api/characters/{id}` | 级联删除角色（events + memories + conversations + growth_logs） |

### 对话交互（2 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/chat` | 与角色对话（Director + Actor 双管线，非流式） |
| `POST` | `/api/chat/stream` | 流式对话（SSE：thinking → meta → speech → done） |

### 会话管理（5 个，参考 NextChat）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/sessions` | 列出会话（按 character_id + 可选 search 模糊） |
| `POST` | `/api/sessions` | 创建新会话 |
| `GET` | `/api/sessions/{id}` | 会话详情（含全部消息） |
| `PATCH` | `/api/sessions/{id}` | 重命名会话 |
| `DELETE` | `/api/sessions/{id}` | 删除会话（级联 conversations） |

### 成长 / 事件 / 时间（5 个，Day 3-4）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/growth/trigger` | 触发角色成长（人格演化分析） |
| `GET` | `/api/characters/{id}/events` | 列出事件（按 day_number / status 过滤） |
| `POST` | `/api/event/advance` | 推进一个 pending 事件（LLM 推演生成 result） |
| `POST` | `/api/time/iterate` | 日迭代：成长 + 生成次日 schedule + day_number+1 |
| `POST` | `/api/time/auto` | 一键推演：先推完所有 pending → 再 iterate |

### 角色数据查询（3 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/characters/{id}/memories` | 记忆列表（按 memory_type 筛选） |
| `GET` | `/api/characters/{id}/conversations` | 对话历史 |
| `GET` | `/api/characters/{id}/growth-logs` | 成长记录列表 |

### 性能监控（4 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/performance/cache-stats` | 响应缓存命中率（size / hits / misses） |
| `POST` | `/api/performance/cache-invalidate` | 清空响应缓存（可选按 character_id） |
| `GET` | `/api/performance/char-data-cache-stats` | 角色基础数据解析缓存（60s TTL + LRU 256） |
| `POST` | `/api/performance/char-data-cache-invalidate` | 清空角色数据解析缓存 |

### LLM 设置（4 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/settings/llm` | 读取 LLM 设置（API Key 脱敏） |
| `GET` | `/api/settings/llm/providers` | 列出所有厂商 + 兜底默认值 |
| `PUT` | `/api/settings/llm` | 更新设置（切换厂商 / 改 Key / 改温度）—— 热更新单例 |
| `POST` | `/api/settings/llm/test` | 测试 LLM 连接（不写盘） |

### API 联通测试（3 个，参考 web-tools）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/test/models` | 拉取 provider 的 `/v1/models` 列表 |
| `POST` | `/api/test/latency` | 流式延迟测试（TTFT + 总耗时） |
| `POST` | `/api/test/probe` | 原始请求探针（含完整 request/response） |

---

## 项目架构

```
CharacterSeed/
├── backend/                          # FastAPI 后端（25 个 /api/* 端点）
│   ├── main.py                       # 入口：启动事件 / CORS / 路由 / 静态托管
│   ├── config.py                     # 配置（环境变量读取）
│   ├── database.py                   # SQLAlchemy 引擎 + Session 工厂
│   ├── models.py                     # ORM（6 张表：characters / chat_sessions /
│   │                                 #         conversations / memories / growth_logs / events）
│   ├── schemas.py                    # Pydantic 请求/响应（20+ 个 schema）
│   ├── api/                          # APIRouter（当前 memory_router 待挂载）
│   ├── crud/                         # 纯 SQL 封装（character / memory / conversation /
│   │                                 #         growth / event 五个模块）
│   ├── modules/                      # 业务模块（Pipeline 模式 + 单例 + 热更新）
│   │   ├── creation.py               #   CreationModule（创建角色）
│   │   ├── interaction.py            #   InteractionPipeline（Director + Actor 双 LLM 交互）
│   │   ├── enhanced_interaction.py   #   EnhancedInteractionPipeline（三层记忆增强版）
│   │   ├── growth.py                 #   GrowthModule（人格演化）
│   │   ├── event.py                  #   EventManager（推进单个事件）
│   │   └── time.py                   #   TimeEngine（迭代 / 一键推演）
│   ├── services/                     # 外部依赖 + 业务封装
│   │   ├── llm_service.py            #   OpenAI 兼容协议封装（多 provider / 热更新）
│   │   ├── llm_settings_store.py     #   LLM 配置 JSON 持久化（5 厂商 + .env 回退）
│   │   ├── llm_api_tester.py         #   API 联通测试（models / latency / probe）
│   │   ├── chat_session_crud.py      #   session CRUD（含 message_count 聚合）
│   │   └── db_migration.py           #   迁移工具（v001_sessions / v002_event_and_character_fields）
│   ├── memory/                       # 三层记忆（短期/长期/知识库）—— 增强管线用
│   ├── prompts/                      # LLM Prompt 模板
│   │   ├── creation.txt              #   角色创建
│   │   ├── director.txt              #   注意力聚焦
│   │   ├── actor.txt                 #   行为生成
│   │   └── growth.txt                #   成长分析
│   └── tests/                        # pytest
├── web/                              # Vue 3 + Vite + TypeScript 前端
│   ├── src/
│   │   ├── api/                      #   后端 API 调用封装（命名空间聚合导出）
│   │   ├── composables/              #   Vue 组合式函数（useCharacters / useChat / useEvents）
│   │   │                             #   状态管理：composable + 模块级 ref 单例（**无 Pinia**）
│   │   ├── router/                   #   路由（/create /chat /events /status /settings）
│   │   ├── views/                    #   页面组件（**无 UI 库**，全部自实现 CSS）
│   │   ├── types/                    #   TypeScript 类型（与 backend/schemas.py 对齐）
│   │   ├── utils/                    #   工具函数
│   │   ├── App.vue                   #   根组件
│   │   └── main.ts                   #   入口
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts                # 代理 + selfHandleResponse（SSE 用）
├── data/
│   └── character_seed.db             # SQLite 数据库（自动生成）
├── doc/
│   ├── 项目方案.md                    # 项目方案文档
│   ├── mvp详细设计.md                 # MVP 详细设计
│   └── graph/                        # 架构图、时序图
├── start_demo.bat                    # 一键启动脚本
├── requirements.txt                  # Python 依赖
├── pyproject.toml                    # 项目元数据
├── .env.example                      # 环境变量模板
└── README.md                         # 本文件
```

### 数据库 Schema

| 表名 | 说明 | 核心字段 |
|---|---|---|
| `characters` | 角色主表 | name, description, world_setting, personality, current_state, speaking_style, values, habits, long_term_goal, day_number, creation_raw |
| `chat_sessions` | 会话（多轮容器） | character_id (FK), title, created_at, updated_at |
| `conversations` | 对话记录 | character_id, session_id, user_input, npc_response, emotion, action, expression, director_raw, actor_raw |
| `memories` | 记忆条目 | character_id, content, importance (1-10), memory_type (conversation/event/growth) |
| `growth_logs` | 成长记录 | character_id, personality_delta, event_summary, new_memories, growth_raw |
| `events` | 事件时间线（Day 4） | character_id, day_number, order_index, event_type, content, status (pending/active/completed), time_period, result_json, session_id |

### 数据流架构

```
用户操作 (Vue SPA, 5173)
    │
    ▼
web/src/api   ──HTTP / SSE──▶  FastAPI 后端 (8000)
                                  │
                                  ├── CreationModule    → LLM → 生成角色
                                  ├── InteractionPipe   → LLM ×2 (Director+Actor) → 对话回复
                                  ├── GrowthModule      → LLM → 人格演化
                                  ├── EventManager      → LLM → 单事件推演 result
                                  └── TimeEngine        → LLM → 次日 schedule（3-5 事件）
                                  │
                                  ▼
                              SQLite 数据库 (6 张表)
```

---

## 人格六维模型

| 维度 | 英文 | 说明 |
|---|---|---|
| 乐观 | Optimism | 角色对未来的积极程度 |
| 勇气 | Courage | 面对挑战和危险的胆量 |
| 同理心 | Empathy | 理解和感受他人情绪的能力 |
| 忠诚 | Loyalty | 对信念、关系或目标的坚持 |
| 智慧 | Intelligence | 逻辑推理和知识运用能力 |
| 社交 | Sociability | 人际交往的主动性和技巧 |

每项数值范围 0–100，通过对话经历动态变化。成长系统依据对话内容分析人格变化方向与幅度。

---

## 常见问题

### 1. LLM 调用失败

确认 `.env` 中当前激活 provider 的 API Key 正确且有余额。
在设置页可一键切换到 5 厂商（DeepSeek / Agnes AI / Qwen / OpenAI / Ollama）中的任意一个。

### 2. 数据库文件位于何处？

`data/character_seed.db`，首次启动自动创建。

### 3. 如何清空数据重新开始？

删除 `data/character_seed.db`，重启后端即可自动新建。
所有表（characters / chat_sessions / conversations / memories / growth_logs / events）会自动重建。

### 4. 前端显示"后端未启动"？

确认 uvicorn 已在端口 8000 运行。可在终端执行：
```bash
uvicorn backend.main:app --reload --port 8000
```

### 5. 如何调试 LLM 输出？

- 前端对话页可展开"LLM 管线内部响应"查看 Director/Actor 原始 JSON
- 前端状态面板可展开每条成长记录的 Growth LLM 原始响应
- 前端事件中心可展开每条事件的 result_json 查看推演回执
- 数据库 `*_raw` 字段保存了所有 LLM 原始 JSON，可用任何 SQLite 工具查看

### 6. 事件中心按钮点击报错？

检查后端日志中是否有 `事件 LLM 调用失败，使用降级` 字样。
- 若频繁出现：当前 provider 网络不稳，可在设置页切换到其他厂商
- 降级逻辑已保证 UI 不卡死，事件会标记为 completed（result 写降级文案）

---

## 许可证

MIT License

---

## 版本历史

| 版本 | 日期 | 内容 |
|---|---|---|
| v1.0 MVP | 2026-06 | 完整 MVP：角色创建 + 双 LLM 对话管线 + 人格成长 + Streamlit 三页面前端 + 一键启动 |
| v1.1    | 2026-06 | 前端切换至 Vue 3 + Vite + TypeScript，移除 Streamlit |
| v1.2    | 2026-06 | Day 2 升级：Director + Actor 双管线 + 6 维人格演化 + GrowthModule |
| v1.3    | 2026-06 | Day 3 升级：NextChat 式多会话管理 + LLM 厂商热切换（5 厂商）+ API 联通测试 Dashboard |
| v1.4    | 2026-06 | Day 4 升级：事件推进 + 时间引擎（`/api/event/advance` `/api/time/iterate` `/api/time/auto`）+ 多维角色画像（speaking_style / values / habits / long_term_goal / day_number） + 响应缓存 + 角色数据解析缓存 |
