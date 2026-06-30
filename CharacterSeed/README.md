# CharacterSeed — AI 数字生命模拟系统

> 基于人格、记忆、世界和时间驱动的 NPC 生命系统。不再是"会说话的聊天机器人"，而是拥有持续成长能力的数字生命。

---

## 核心特性

| 特性 | 说明 |
|---|---|
| 🌱 **Creation System** | 一句话描述或 TXT 故事文件 → 生成完整角色（名称、世界设定、6 维人格、初始记忆） |
| 🎭 **Director + Actor 双 LLM 管线** | Director 负责"注意力聚焦"（情绪/目标/风格），Actor 负责"行为生成"（动作/表情/语言）——可解释、可调试 |
| 🔥 **Jiwen 积温引擎** | 五轴连续状态模型（connection/pride/valence/arousal/immersion），数学漂移 + 阈值触发，驱动角色主动消息 |
| 💬 **主动消息系统** | SSE 实时推送，懒会话绑定，异步 LLM 生成，角色可在用户离线时主动发起对话 |
| 📅 **事件推进 + 世界引擎** | 事件驱动世界状态变化，世界反作用于角色，形成闭环 |
| 🧠 **人格演化与成长** | 对话经历驱动人格六维属性动态变化，所有成长记录可追溯 |
| 🌍 **世界系统** | 地点、物品、关系网络，事件在世界中发生，世界的变化反作用于角色 |
| 🎨 **头像生成** | 基于 Agnes AI 图像模型，自动生成角色自画像头像 |
| 💾 **多维角色画像** | speaking_style / values / habits / long_term_goal / day_number 全部持久化 |
| 📊 **全功能 Web 前端** | React 18 + Vite 6 + TypeScript + Tailwind CSS + lucide-react 图标，多页面 SPA |

---

## 架构概览

```
React SPA (5173)  ────HTTP/SSE────▶  FastAPI (8000)
                                           │
                                           ├── 单例：_creation_module
                                           ├── 单例：_pipeline (Director + Actor)
                                           ├── JiwenEngine (五轴状态 + 调度器)
                                           ├── ProactiveMessenger (SSE 推送)
                                           ├── 单例：_growth_module
                                           ├── EventManager + WorldEngine
                                           └── SQLite (data/character_seed.db)
```

开发期走 Vite 代理（`web/react-vite/vite.config.ts`），生产期前后端同源部署在 8000（FastAPI `StaticFiles` 托管 `web/react-vite/dist`）。

---

## 技术栈

| 层级 | 技术 | 版本 |
|---|---|---|
| 后端框架 | FastAPI | 0.104.1 |
| 前端框架 | React 18 + Vite 6 + TypeScript | 18.3.1 / 6.x |
| CSS 框架 | Tailwind CSS v4 + 自实现 CSS 变量 | — |
| 图标 | lucide-react | 0.460.0 |
| 图表 | recharts | 2.15.0 |
| ORM | SQLAlchemy | 2.0.23 |
| 数据库 | SQLite | — |
| LLM | OpenAI 兼容协议 | openai>=1.30.0 |
| Python | 3.11+ | — |
| Node | 18+（推荐 22+） | — |

**前端依赖**（无 UI 组件库）：
- `react-router-dom` — 路由
- `lucide-react` — 图标
- `recharts` — 趋势图表
- `marked` + `dompurify` — Markdown 渲染 + XSS 防护

---

## 产品功能

### 🌱 角色创建页
- 文本输入 / TXT 文件上传双模式
- Creation LLM 自动生成：名称、世界设定、6 维人格、初始记忆
- 结果卡片展示 + LLM 原始响应（可展开调试）
- 创建成功 Toast 提示 + 自动跳转对话页

### 💬 对话交互页
- 角色选择器 + 聊天气泡界面
- Director → Actor 双管线实时生成 NPC 回复
- 每条回复附带：情绪标签、动作描述、表情
- 可展开查看 Director/Actor 原始 JSON 输出
- 支持 SSE 流式输出（`/api/chat/stream`）
- 主动消息推送（SSE 连接，角色离线时主动发起）

### 📊 角色状态页
- 人格 6 维进度条可视化
- 当前状态（位置/活动/心情）
- 世界设定 + 长期目标
- 角色画像标签（说话风格 / 核心信念 / 日常习惯）
- 记忆列表（按类型筛选：对话/事件/成长）
- 对话历史记录
- 成长记录折叠面板（人格 delta + 新增记忆）

### 📅 事件中心
- 按 Day 分组的事件时间线（morning/afternoon/evening/night）
- 3 个核心操作：
  - **推进一个** → LLM 推演单个 pending 事件，生成回执
  - **迭代到下一天** → 成长分析 + 生成次日 schedule（3-5 事件）+ 落库
  - **一键推演** → 串联"先推完 pending → 再迭代"

### 🌍 世界面板
- 世界状态总览
- 地点管理（创建/编辑/删除）
- 物品管理
- 角色关系网络

### 🔥 积温面板（Jiwen）
- 五轴状态实时可视化
- 阈值触发配置
- 调度器控制（启动/停止/降级模式）
- 主动消息队列查看

### ⚙️ 设置面板
- 5 厂商一键切换（DeepSeek / Agnes AI / Qwen / OpenAI / Ollama）
- API Key 脱敏显示（保留首尾 4 字符）
- "测试连接" 按钮：填完 Key 立即验证，不用重启
- API 联通测试 Dashboard：models 列表 / 流式延迟 / 原始请求探针
- 角色级 Jiwen 参数覆盖（速率 + 阈值）

---

## 前后端通信

### 前端路由（`web/react-vite/src/router/routes.js`）

| 路径 | 标题 | 说明 |
|---|---|---|
| `/chat` | 对话 | 默认路由，核心交互页 |
| `/create` | 创建角色 | 角色创建（建议移出侧边栏，改为顶栏 "+" 按钮） |
| `/status` | 角色状态 | 角色人格/状态/记忆/对话/成长总览 |
| `/events` | 事件 | 事件推进中心 |
| `/memory` | 记忆 | 记忆查看器（短/长期/知识库 + 类型筛选） |
| `/settings` | 设置 | LLM 配置 |
| `/growth` | 成长 | 人物成长页面 |
| `/logs` | 系统 | 日志浏览 + 系统概述（合并原"状态"页） |
| `/world` | 世界 | 世界面板 |
| `/character/:id` | 角色详情 | 角色详情页（含头像 + 雷达图） |

### 后端 API 端点（40+ 个）

启动后端后访问 http://localhost:8000/docs 可查看交互式 API 文档。

#### 角色管理（4 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/characters/create` | 创建角色（文本描述或 TXT 文件上传） |
| `GET` | `/api/characters` | 获取角色列表（分页：skip / limit） |
| `GET` | `/api/characters/{id}` | 获取角色详情（含人格、状态、day_number、画像、头像字段） |
| `DELETE` | `/api/characters/{id}` | 级联删除角色（events + memories + conversations + growth_logs） |

#### 对话交互（2 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/chat` | 与角色对话（Director + Actor 双管线，非流式） |
| `POST` | `/api/chat/stream` | 流式对话（SSE：thinking → meta → speech → done） |

#### 会话管理（5 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/sessions` | 列出会话（按 character_id + 可选 search 模糊） |
| `POST` | `/api/sessions` | 创建新会话 |
| `GET` | `/api/sessions/{id}` | 会话详情（含全部消息） |
| `PATCH` | `/api/sessions/{id}` | 重命名会话 |
| `DELETE` | `/api/sessions/{id}` | 删除会话（级联 conversations） |

#### Jiwen 积温系统（24 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/jiwen/characters/{id}/state` | 获取角色积温状态 |
| `GET` | `/api/jiwen/characters/{id}/params` | 获取角色级积温参数 |
| `PUT` | `/api/jiwen/characters/{id}/params` | 更新角色级积温参数 |
| `POST` | `/api/jiwen/characters/{id}/tick` | 手动触发积温 tick |
| `GET` | `/api/jiwen/characters/{id}/triggers` | 获取触发记录 |
| `GET` | `/api/jiwen/scheduler/status` | 获取调度器状态 |
| `POST` | `/api/jiwen/scheduler/start` | 启动调度器 |
| `POST` | `/api/jiwen/scheduler/stop` | 停止调度器 |
| `POST` | `/api/jiwen/proactive/send` | 手动发送主动消息 |
| `GET` | `/api/jiwen/proactive/messages` | 获取主动消息列表 |
| `POST` | `/api/jiwen/proactive/consume` | 消费主动消息（标记已读） |
| ... | ... | （完整 endpoints 见 `/docs` Swagger） |

#### 成长 / 事件 / 时间（5 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/growth/trigger` | 触发角色成长（人格演化分析） |
| `GET` | `/api/characters/{id}/events` | 列出事件（按 day_number / status 过滤） |
| `POST` | `/api/event/advance` | 推进一个 pending 事件（LLM 推演生成 result） |
| `POST` | `/api/time/iterate` | 日迭代：成长 + 生成次日 schedule + day_number+1 |
| `POST` | `/api/time/auto` | 一键推演：先推完所有 pending → 再 iterate |

#### 世界系统（10+ 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/worlds/{id}` | 获取世界详情 |
| `GET` | `/api/worlds/{id}/state` | 获取世界状态 |
| `GET` | `/api/worlds/{id}/locations` | 获取地点列表 |
| `POST` | `/api/worlds/{id}/locations` | 创建地点 |
| `GET` | `/api/worlds/{id}/items` | 获取物品列表 |
| `POST` | `/api/worlds/{id}/items` | 创建物品 |
| `GET` | `/api/worlds/{id}/relationships` | 获取关系网络 |
| ... | ... | （完整 endpoints 见 `/docs` Swagger） |

#### 角色数据查询（3 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/characters/{id}/memories` | 记忆列表（按 memory_type 筛选） |
| `GET` | `/api/characters/{id}/conversations` | 对话历史 |
| `GET` | `/api/characters/{id}/growth-logs` | 成长记录列表 |

#### 头像生成（4 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/characters/{id}/avatar/generate` | 触发头像生成 |
| `GET` | `/api/characters/{id}/avatar/status` | 查询头像生成状态 |
| `POST` | `/api/characters/{id}/avatar/select` | 选择头像候选 |
| `POST` | `/api/characters/{id}/avatar/video` | 生成头像视频 |

#### 性能监控（4 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/performance/cache-stats` | 响应缓存命中率（size / hits / misses） |
| `POST` | `/api/performance/cache-invalidate` | 清空响应缓存 |
| `GET` | `/api/performance/char-data-cache-stats` | 角色基础数据解析缓存 |
| `POST` | `/api/performance/char-data-cache-invalidate` | 清空角色数据解析缓存 |

#### LLM 设置（4 个）
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/settings/llm` | 读取 LLM 设置（API Key 脱敏） |
| `GET` | `/api/settings/llm/providers` | 列出所有厂商 + 兜底默认值 |
| `PUT` | `/api/settings/llm` | 更新设置（热更新单例） |
| `POST` | `/api/settings/llm/test` | 测试 LLM 连接（不写盘） |

#### 系统状态（1 个，合并至日志页）
| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/status` | 系统仪表盘数据（角色总数、活跃会话、平均响应、缓存命中率、LLM 服务状态、活动日志） |

---

## 数据库 Schema

| 表名 | 说明 | 核心字段 |
|---|---|---|
| `characters` | 角色主表 | `name`, `description`, `world_setting`, `personality_json`, `current_state_json`, `speaking_style`, `values`, `habits`, `long_term_goal`, `day_number`, `creation_raw`, `config` (v008), `appearance` (v009), `avatar_url`, `avatar_generated_at` |
| `chat_sessions` | 会话（多轮容器） | `character_id` (FK), `title`, `created_at`, `updated_at` |
| `conversations` | 对话记录 | `character_id`, `session_id`, `user_input`, `npc_response`, `emotion`, `action`, `expression`, `director_raw`, `actor_raw` |
| `memories` | 记忆条目 | `character_id`, `content`, `importance` (1-10), `memory_type` (conversation/event/growth), `created_at` |
| `growth_logs` | 成长记录 | `character_id`, `personality_delta`, `event_summary`, `new_memories`, `world_changes_json`, `schedule_json`, `growth_raw`, `created_at` |
| `events` | 事件时间线 | `character_id`, `day_number`, `order_index`, `event_type`, `content`, `status` (pending/active/completed), `time_period`, `result_json`, `session_id` |
| `jiwen_states` | 积温状态（v007） | `character_id`, `connection`, `pride`, `valence`, `arousal`, `immersion`, `updated_at` |
| `jiwen_triggers` | 积温触发记录（v007） | `character_id`, `trigger_type`, `triggered_at`, `consumed` |
| `worlds` | 世界主表（v004） | `character_id` (FK), `name`, `description` |
| `locations` | 地点（v004） | `world_id` (FK), `name`, `description`, `attributes_json` |
| `items` | 物品（v004） | `world_id` (FK), `name`, `description`, `attributes_json` |
| `relationships` | 关系（v004） | `world_id` (FK), `source_id`, `target_id`, `relation_type`, `strength` |

### 数据库迁移

迁移脚本位于 `backend/services/db_migration.py`，当前最新版本 **v009**：

| 版本 | 说明 |
|---|---|
| v001 | 初始表结构（characters / chat_sessions / conversations / memories / growth_logs / events） |
| v002 | 新增 proficiency 字段（技能熟练度） |
| v003 | 新增 interaction_stats 表 |
| v004 | 世界系统（worlds / locations / items / relationships） |
| v005 | Jiwen 积温引擎（jiwen_states / jiwen_triggers） |
| v006 | 会话增强（message_count / last_message_preview） |
| v007 | 角色配置字段（config JSON） |
| v008 | 头像生成字段（avatar_url / appearance / avatar_*） |
| v009 | 积温参数角色级覆盖 |

迁移幂等可重跑，首次启动自动执行。

---

## 环境配置

### 1. 前置要求

- Python 3.11 或更高版本
- Node.js 18+（推荐 22+）
- 任意 LLM Provider API Key（DeepSeek / Agnes AI / Qwen / OpenAI / Ollama）

### 2. 安装依赖

```bash
# 进入项目目录
cd CharacterSeed

# 后端：创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# 安装 Python 依赖
pip install -r requirements.txt

# 前端：安装 Node 依赖
cd web/react-vite
npm install
cd ../..
```

> 若使用 uv 管理 Python 依赖，可执行 `uv sync`。

### 3. 配置 API Key

```bash
# 复制环境变量模板
copy .env.example .env         # Windows
# cp .env.example .env         # macOS / Linux

# 编辑 .env 文件，填入你选定的 provider 的 API Key
# 5 选 1：DEEPSEEK / AGNES / QWEN / OPENAI / OLLAMA
# AGNES_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`.env` 文件内容示例：

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

### 方式一：分步启动（推荐）

```bash
# 终端 1：启动后端
cd CharacterSeed
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 终端 2：启动前端
cd CharacterSeed/web/react-vite
npm run dev
```

访问以下地址：

| 服务 | 地址 |
|---|---|
| 前端页面 | http://127.0.0.1:5173 |
| 后端 API 文档 (Swagger) | http://127.0.0.1:8000/docs |
| 后端健康检查 | http://127.0.0.1:8000/ |

### 方式二：生产构建

```bash
# 构建前端
cd CharacterSeed/web/react-vite
npm run build

# 启动后端（自动托管前端 dist）
cd ../..
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

生产环境访问 http://127.0.0.1:8000 即可（前端由 FastAPI 静态托管）。

---

## 项目结构

```
CharacterSeed/
├── backend/                          # FastAPI 后端（40+ 个 /api/* 端点）
│   ├── main.py                       # 入口：启动事件 / CORS / 路由 / 静态托管
│   ├── config.py                     # 配置（环境变量读取）
│   ├── database.py                   # SQLAlchemy 引擎 + Session 工厂
│   ├── models.py                     # ORM（12 张表）
│   ├── schemas.py                    # Pydantic 请求/响应（30+ 个 schema）
│   ├── api/                          # APIRouter 按业务分组
│   │   ├── character_router.py      #   角色管理（4 个）
│   │   ├── chat_router.py           #   对话交互（2 个）
│   │   ├── session_router.py        #   会话管理（5 个）
│   │   ├── jiwen_router.py          #   积温系统（24 个）
│   │   ├── growth_router.py         #   成长（1 个）
│   │   ├── event_router.py          #   事件（1 个）
│   │   ├── time_router.py           #   时间引擎（2 个）
│   │   ├── world_router.py          #   世界系统（10+ 个）
│   │   ├── memory_router.py         #   记忆（1 个）
│   │   ├── settings_router.py       #   LLM 设置（4 个）
│   │   └── performance_router.py    #   性能监控（4 个）
│   ├── crud/                         # 纯 SQL 封装（character / memory / conversation / growth / event / world）
│   ├── modules/                      # 业务模块（Pipeline 模式 + 单例 + 热更新）
│   │   ├── creation.py               #   CreationModule（创建角色）
│   │   ├── interaction.py            #   InteractionPipeline（Director + Actor 双 LLM 交互）
│   │   ├── enhanced_interaction.py   #   EnhancedInteractionPipeline（三层记忆增强版）
│   │   ├── growth.py                 #   GrowthModule（人格演化）
│   │   ├── event.py                  #   EventManager（推进单个事件）
│   │   ├── time.py                   #   TimeEngine（迭代 / 一键推演）
│   │   └── proactive.py              #   ProactiveMessenger（主动消息）
│   ├── jiwen/                        # 积温引擎
│   │   ├── jiwen_core.py             #   五轴状态模型 + 数学漂移
│   │   ├── jiwen_manager.py          #   状态持久化 + 阈值触发
│   │   └── jiwen_scheduler.py       #   后台调度器（正常/降级/恢复 三模式）
│   ├── services/                     # 外部依赖 + 业务封装
│   │   ├── llm_service.py            #   OpenAI 兼容协议封装（多 provider / 热更新）
│   │   ├── llm_settings_store.py     #   LLM 配置 JSON 持久化（5 厂商 + .env 回退）
│   │   ├── avatar_generation_service.py # Agnes AI 头像生成
│   │   ├── agnes_client.py           #   Agnes AI API 客户端
│   │   └── db_migration.py           #   迁移工具（v001-v009）
│   ├── memory/                       # 三层记忆（短期/长期/知识库）
│   ├── prompts/                      # LLM Prompt 模板
│   │   ├── creation.txt              #   角色创建
│   │   ├── director.txt              #   注意力聚焦
│   │   ├── actor.txt                 #   行为生成
│   │   └── growth.txt                #   成长分析
│   └── tests/                        # pytest
├── web/                              # 前端（双版本，当前主用 React-Vite）
│   ├── react-vite/                   #   React 18 + Vite 6 + TypeScript（当前主用）
│   │   ├── src/
│   │   │   ├── pages/               #   页面组件（ChatPage / CreatePage / StatusPage / ...）
│   │   │   ├── components/          #   可复用组件（CharacterAvatar / PersonalityRadar / ...）
│   │   │   ├── hooks/               #   React Hooks（useCharacters / useToast / ...）
│   │   │   ├── utils/               #   工具函数 + API 客户端
│   │   │   ├── router/              #   路由配置
│   │   │   └── styles.css           #   全局样式（~5000 行）
│   │   ├── package.json
│   │   └── vite.config.ts
│   └── src/                          #   Vue 3 + Vite（旧版，已停用）
├── data/
│   └── character_seed.db             # SQLite 数据库（自动生成）
├── doc/                              # 设计文档
├── outputs/                          # 输出文档（审计计划 / 修复计划 / ...）
├── .workbuddy/                       # WorkBuddy AI 助手配置
│   └── harness/                      #   AI 开发防护系统（feature_registry / core_lock / ...）
├── start_demo.bat                    # 一键启动脚本（Windows）
├── requirements.txt                  # Python 依赖
├── .env.example                      # 环境变量模板
└── README.md                         # 本文件
```

---

## 人格六维模型

| 维度 | 英文 | 说明 |
|---|---|---|
| 亲和力 | Affability | 角色与他人相处的友好程度 |
| 攻击性 | Aggressiveness | 面对冲突时的进攻倾向 |
| 同理心 | Empathy | 理解和感受他人情绪的能力 |
| 好奇心 | Curiosity | 探索未知和学习的欲望 |
| 创造力 | Creativity | 想象力和创新思维 |
| 稳定性 | Stability | 情绪和行为的可预测程度 |

每项数值范围 0–100，通过对话经历动态变化。成长系统依据对话内容分析人格变化方向与幅度。

---

## Jiwen 积温引擎

Jiwen（积温）是 CharacterSeed 的核心创新之一，模拟角色情感的"积温"效应：

### 五轴状态模型

| 轴 | 说明 | 漂移方向 |
|---|---|---|
| `connection` | 与用户的连接感 | 随时间自然衰减 |
| `pride` | 自尊/自豪感 | 随正面反馈上升 |
| `valence` | 情绪效价（正/负） | 受对话内容影响 |
| `arousal` | 情绪唤醒度 | 受事件强度影响 |
| `immersion` | 沉浸度（投入程度） | 随互动深度增加 |

### 阈值触发

当任意轴超过设定阈值时，触发：
- **主动消息**：角色主动发起对话
- **行为变化**：对话风格临时偏移
- **记忆强化**：相关记忆重要性提升

### 调度器三模式

| 模式 | 说明 |
|---|---|
| `normal` | 正常间隔 tick（默认 300s） |
| `degraded` | 降级模式（网络异常时自动切换，默认 900s） |
| `recovery` | 恢复模式（从降级恢复，默认 300s） |

---

## 常见问题

### 1. LLM 调用失败

确认 `.env` 中当前激活 provider 的 API Key 正确且有余额。
在设置页可一键切换到 5 厂商（DeepSeek / Agnes AI / Qwen / OpenAI / Ollama）中的任意一个。

### 2. 数据库文件位于何处？

`data/character_seed.db`，首次启动自动创建。

### 3. 如何清空数据重新开始？

删除 `data/character_seed.db`，重启后端即可自动新建。
所有表会自动重建（通过 `db_migration.py` 幂等迁移）。

### 4. 前端显示"后端未启动"？

确认 uvicorn 已在端口 8000 运行。可在终端执行：
```bash
uvicorn backend.main:app --reload --port 8000
```

### 5. 如何调试 LLM 输出？

- 前端对话页可展开"LLM 管线内部响应"查看 Director/Actor 原始 JSON
- 前端角色状态页可展开每条成长记录的 Growth LLM 原始响应
- 前端事件中心可展开每条事件的 result_json 查看推演回执
- 数据库 `*_raw` 字段保存了所有 LLM 原始 JSON，可用任何 SQLite 工具查看

### 6. 事件中心按钮点击报错？

检查后端日志中是否有 `事件 LLM 调用失败，使用降级` 字样。
- 若频繁出现：当前 provider 网络不稳，可在设置页切换到其他厂商
- 降级逻辑已保证 UI 不卡死，事件会标记为 completed（result 写降级文案）

### 7. 如何回滚到之前的版本？

```bash
git log --oneline  # 查看提交历史
git reset --hard <commit-sha>  # 回滚到指定提交
```

建议在重大修改前先 `git add -A && git commit -m "备份：xxx"` 创建快照。

---

## 开发指南

### 添加新 API 端点

1. 在 `backend/api/` 下找到对应业务的 router 文件
2. 使用 `@router.get/post` 装饰器添加端点
3. 在 `backend/schemas.py` 中定义请求/响应 schema
4. 在 `backend/main.py` 中挂载 router（如未挂载）

### 添加新前端页面

1. 在 `web/react-vite/src/pages/` 下创建页面组件
2. 在 `web/react-vite/src/router/routes.js` 中添加路由
3. 在 `web/react-vite/src/router/lazyPages.js` 中添加懒加载入口

### 数据库迁移

1. 在 `backend/services/db_migration.py` 中添加 `migrate_vXXX_xxx()` 函数
2. 在 `run_migrations()` 中的 `MIGRATIONS` 列表末尾追加
3. 重启后端，迁移自动执行

---

## 版本历史

| 版本 | 日期 | 内容 |
|---|---|---|
| v1.0 MVP | 2026-06 | 完整 MVP：角色创建 + 双 LLM 对话管线 + 人格成长 + Streamlit 三页面前端 |
| v1.1 | 2026-06 | 前端切换至 Vue 3 + Vite + TypeScript，移除 Streamlit |
| v1.2 | 2026-06 | Day 2 升级：Director + Actor 双管线 + 6 维人格演化 + GrowthModule |
| v1.3 | 2026-06 | Day 3 升级：多会话管理 + LLM 厂商热切换（5 厂商）+ API 联通测试 |
| v1.4 | 2026-06 | Day 4 升级：事件推进 + 时间引擎 + 多维角色画像 + 响应缓存 |
| v2.0 | 2026-06 | 前端重构：Vue → React 18 + Vite 6 + TypeScript + Tailwind CSS |
| v2.5 | 2026-06 | Jiwen 积温引擎上线：五轴状态 + 阈值触发 + 主动消息系统 |
| v2.8 | 2026-06 | 世界系统完成：地点/物品/关系网络 + 事件驱动世界状态变化 |
| v3.0 | 2026-06 | 头像生成系统：Agnes AI 图像模型 + 候选选择 + 头像视频生成 |

---

## 许可证

MIT License

---

## 贡献

欢迎提交 Issue 和 Pull Request！

在提交 PR 前，请确保：
1. 代码通过现有测试（`pytest`）
2. 新增功能包含相应测试
3. 数据库迁移幂等可重跑
4. API 文档（Swagger）已更新

---

## 联系方式

- GitHub Issues：https://github.com/joker1point/luyan/issues
- 项目主页：https://github.com/joker1point/luyan
