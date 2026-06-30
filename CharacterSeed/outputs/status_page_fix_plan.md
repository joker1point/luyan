# 状态页修复计划 — "名不副实" 到 "名副其实"

## 现状诊断

| 文件 | 路由 | 导航名 | 实际内容 |
|------|------|--------|----------|
| `StatusPage.jsx` | `/status` | 状态 | 系统仪表盘（角色总数、活跃会话、平均响应、LLM 状态、缓存统计、活动日志） |
| `StatusView.vue` | 不存在于 React | — | 角色状态（6维人格、当前位置、世界设定、长期目标、标签、记忆、对话、成长） |

**问题**：用户点击"状态"，预期看到角色的身体状态 / 心理状态 / 人格状态，实际看到的是后端性能面板。命名与内容完全错位。

---

## 修复方案

分 3 个阶段，涉及 **6 个文件修改 + 1 个新文件**。

---

### Phase 1：系统仪表盘并入日志页

目标：把当前 `StatusPage.jsx` 的"系统状态仪表盘"内容移入 `LogsPage.jsx`，作为日志页内的一个 Tab。

#### 1.1 LogsPage 增加「系统概述」Tab

**文件**：`web/react-vite/src/pages/LogsPage.jsx`

**改动**：
- 页面顶部增加 Tab 切换：`日志浏览` | `系统概述`
- 「系统概述」Tab 内嵌当前 StatusPage 的全部内容（MetricCard 网格 → 响应时间趋势图 → LLM 服务 + 缓存统计 → 活动日志）
- tab="overview" 时调用 `api.getStatus()` 获取仪表盘数据
- 不影响现有日志浏览功能

**数据源**：
```js
// LogsPage 新增
const [systemData, setSystemData] = useState(null)
const [systemLoading, setSystemLoading] = useState(false)

const loadSystemStatus = useCallback(async () => {
  setSystemLoading(true)
  try {
    const d = await api.getStatus()
    setSystemData(d)
  } finally {
    setSystemLoading(false)
  }
}, [api])
```

**Tab 结构**：
```
┌─────────────────────────────────────────────┐
│  [ 日志浏览 ]  [ 系统概述 ]                  │
├─────────────────────────────────────────────┤
│  (当 tab = 'overview')                      │
│  ┌──────────────────────────────────────┐   │
│  │ MetricCard × 4 网格                  │   │
│  │ 响应时间趋势 AreaChart               │   │
│  │ LLM 服务  缓存统计                  │   │
│  │ 活动日志                             │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

#### 1.2 StatusPage 精简为角色状态页（暂留壳）

**文件**：`web/react-vite/src/pages/StatusPage.jsx`

**改动**：**全文重写**（当前 261 行全部替换，内容移至 LogsPage 的 tab）

新内容 → 角色状态页（Phase 2 详细定义）

#### 1.3 路由导航更新

**文件**：`web/react-vite/src/router/routes.js`

```diff
// 第 13-20 行当前状态：
- { path: '/status',   title: '状态',      pageKey: 'status',   showInNav: true  },
+ { path: '/status',   title: '角色状态',  pageKey: 'status',   showInNav: true  },
- { path: '/logs',     title: '日志',      pageKey: 'logs',     showInNav: true  },
+ { path: '/logs',     title: '系统',      pageKey: 'logs',     showInNav: true  },
```

导航名"日志"→"系统"（涵盖日志浏览 + 系统概述），"状态"→"角色状态"（精确语义）。

#### 1.4 保持旧路径兼容

**文件**：`web/react-vite/src/router/index.jsx`

无需修改 — `pageKey: 'status'` 仍指向 `lazyPages.status`，只是组件内容变了。`lazyPages.js` 也无需改动（import 路径不变）。

---

### Phase 2：新角色状态页（核心）

目标：把 Vue `StatusView.vue` 的角色状态总览完整移植到 React。

#### 2.1 组件结构

**文件**：`web/react-vite/src/pages/StatusPage.jsx`（全文重写）

**页面布局**：

```
┌─────────────────────────────────────────────────┐
│  🎭 角色状态 · 林雨棠            [删除角色]      │
│  Day 3 · 记忆 12 条 · 对话 28 条 · 成长 5 条    │
├───────────────────────┬─────────────────────────┤
│  🎭 6 维人格           │  🏷️ 角色画像            │
│  ┌─────────────────┐  │  说话风格: [慵懒] [毒舌] │
│  │ 亲和力  ████  78 │  │  核心信念: [自由] [真实] │
│  │ 攻击性  ██    42 │  │  日常习惯: [晚睡] [咖啡] │
│  │ 同理心  █████ 88 │  │                         │
│  │ 好奇心  ███   56 │  ├─────────────────────────┤
│  │ 创造力  ████  72 │  │  🧠 记忆                 │
│  │ 稳定性  ████  80 │  │  [全部] [对话] [事件][成长]│
│  └─────────────────┘  │  ┌─────────────────────┐ │
│                        │  │ 💬 "我喜欢下雨天"    │ │
│  📍 当前状态            │  │ 重要度 7/10  · 2h前  │ │
│  ┌──────┬──────┬────┐ │  └─────────────────────┘ │
│  │位置  │天气  │情绪│ │  ┌─────────────────────┐ │
│  │咖啡馆│小雨  │低落│ │  │ ⚡ "雨中等公交车"    │ │
│  └──────┴──────┴────┘ │  │ 重要度 5/10  · 5h前  │ │
│                        │  └─────────────────────┘ │
│  🌍 世界设定             │                         │
│  "近未来科幻都市..."    │  💬 对话历史 (最近 20 条) │
│                        │  👤 你好吗？              │
│  🎯 长期目标             │  🤖 还好...              │
│  "成为独立设计师..."    │  👤 今天天气...           │
│                        │  🤖 下雨了...             │
│                        │                         │
│                        │  🌱 成长记录              │
│                        │  ▶ Day 2 学会了新技能...  │
│                        │  ▶ Day 1 初到城市...      │
└───────────────────────┴─────────────────────────┘
```

#### 2.2 数据源

所有数据从 `useCharactersContext()` 拿 active 角色，通过 `api` 并行拉取：

```js
const [data, setData] = useState({
  character: null,     // api.getCharacter(id) — 基础信息 + 人格 + 状态 + 世界设定 + 目标
  memories: [],        // api.getMemory(id) — 记忆列表
  conversations: [],   // api.getConversations(id, { limit: 20 }) — 对话历史
  growthLogs: [],      // api.getGrowthLogs(id, { limit: 50 }) — 成长记录
})
```

**关键 API 字段映射**（来自 `realApi.js`）：

| 数据 | API 调用 | 返回字段 |
|------|----------|----------|
| 角色基础信息 | `api.getCharacter(id)` | `name`, `day_number`, `personality_json` (JSON str), `current_state_json` (JSON str), `world_setting`, `long_term_goal`, `speaking_style` (JSON str), `values` (JSON str), `habits` (JSON str) |
| 记忆列表 | `api.getMemory(id)` | `items[]` → `id`, `memory_type`, `content`, `importance`, `created_at` |
| 对话历史 | `api.getConversations(id, {limit:20})` | `items[]` → `id`, `user_input`, `npc_response`, `timestamp` |
| 成长记录 | `api.getGrowthLogs(id, {limit:50})` | `items[]` → `id`, `event_summary`, `personality_delta`, `new_memories`, `world_changes_json`, `schedule_json`, `growth_raw`, `created_at` |
| 计数统计 | 从上面 4 个 API 的数组长度计算 | `memoryCount`, `conversationCount`, `growthCount` |

#### 2.3 需要新建的子组件

##### CharacterPersonalityPanel.jsx

```jsx
// 6 维人格进度条面板
// Props: personality: Record<string, number>
// 渲染 6 根进度条，按 value 降序排列
// 格式: [标签] ████████ 78%
```

##### CharacterStateGrid.jsx

```jsx
// 当前位置状态卡片网格
// Props: state: Record<string, any>
// 渲染 Bento Grid: location / weather / mood / 自定义字段
// 带 emoji 图标映射 ("location"→📍, "weather"→🌤️, "mood"→😶)
```

##### CharacterTagBlock.jsx

```jsx
// 标签组（说话风格 / 核心信念 / 日常习惯）
// Props: title, items: string[], accent: string
// 渲染为彩色 chip 列表
```

#### 2.4 重用的现有组件

| 组件 | 用途 | 文件 |
|------|------|------|
| `PersonalityRadar` | 可选：雷达图替代进度条（放 PersonalityPanel 旁边） | `components/PersonalityRadar.jsx` |
| `CharacterAvatar` | 左上角角色头像 | `components/CharacterAvatar.jsx` |
| `MemoryCard` | 记忆列表项 | `components/MemoryCard.jsx` |
| `EmptyState` | 空数据占位 | `components/EmptyState.jsx` |
| `StatusDot` | （不在此页使用） | — |

#### 2.5 角色选择逻辑

如果 `CharactersContext` 里有 active 角色 → 直接用它
如果没有 → 显示空状态「请先选择角色」，指引去 `/chat` 或 `/create`

```jsx
const { characters, activeId } = useCharactersContext()
const characterId = activeId

if (!characterId) {
  return <EmptyState icon={Sparkles} title="请先选择角色" 
    description="在对话页或创建角色后即可查看角色状态" />
}
```

#### 2.6 删除角色功能

保留 Vue 版的删除按钮（带二次确认 + 成功后清除 active 并跳转 `/chat`）：

```js
const handleDelete = async () => {
  if (!window.confirm(`确定删除角色「${character.name}」及其全部记忆/对话/成长记录？此操作不可恢复！`)) return
  await api.deleteCharacter(characterId)
  // 清除 active，刷新角色列表
  setActive(null)
  refresh()
  navigate('/chat')
}
```

---

### Phase 3：样式整合与验证

#### 3.1 新 CSS 类名

**文件**：`web/react-vite/src/styles.css`（追加 ~200 行）

新增类名（避免与现有 `.status-*` 类名冲突，因为这些已被系统仪表盘占用）：

```css
/* 角色状态页 */
.char-status-grid { ... }        /* 左右双列布局 */
.char-status-col-left { ... }    /* 左列：人格 + 状态 + 世界 + 目标 */
.char-status-col-right { ... }   /* 右列：标签 + 记忆 + 对话 + 成长 */

/* 人格面板 */
.personality-panel { ... }
.personality-bar-row { ... }
.personality-bar-label { ... }
.personality-bar-track { ... }
.personality-bar-fill { ... }
.personality-bar-value { ... }

/* 状态网格 */
.state-bento { ... }
.state-bento-cell { ... }
.state-bento-key { ... }
.state-bento-val { ... }

/* 标签组 */
.tag-section { ... }
.tag-section-title { ... }
.tag-chip-group { ... }

/* 记忆、对话、成长列表（复用现有 .memory-list / .conv-list / .growth-list 或调整） */
```

注意：现有的 `.status-container` / `.status-section` / `.metric-grid` 等类名保持不变 → 它们在 LogsPage 的系统概述 Tab 中继续使用。

#### 3.2 响应式断点

```css
@media (max-width: 980px) {
  .char-status-grid { grid-template-columns: 1fr; }
}
```

---

### 修改清单总览

| 阶段 | 文件 | 操作 | 影响行数 |
|------|------|------|----------|
| P1 | `routes.js` | 改 2 行（title 字段） | +2/-2 |
| P1 | `LogsPage.jsx` | 新增 Tab 结构 + 系统概述内容 | +~120 |
| P2 | `StatusPage.jsx` | **全文重写**（261→~280 行） | +280/-261 |
| P2 | `components/CharacterPersonalityPanel.jsx` | **新建** | +~50 |
| P2 | `components/CharacterStateGrid.jsx` | **新建** | +~60 |
| P2 | `components/CharacterTagBlock.jsx` | **新建** | +~35 |
| P3 | `styles.css` | 追加角色状态页样式 | +~200 |
| — | `lazyPages.js` | **无需修改** | 0 |
| — | `router/index.jsx` | **无需修改** | 0 |
| — | `App.jsx` | **无需修改** | 0 |

**总计**：6 个文件修改 + 3 个新组件，净增约 490 行。

---

### 最终导航顺序

修复完成后，侧边栏变为：

```
┌─────────────────────────────┐
│  💬 对话                    │  ← #1 核心
│  📊 角色状态 ───NEW──────── │  ← #2 角色实时状态（名副其实）
│  ⚡ 事件                    │  ← #3 事件推进
│  🌍 世界                    │  ← #4 世界（卖点，紧邻事件）
│  🌱 成长                    │  ← #5 长期追踪
│  🧠 记忆                    │  ← #6 深度查阅
│  ───────────────────────    │
│  ⚙️ 系统 ───(旧"日志")───  │  ← #7 日志浏览 + 系统仪表盘
│  🔧 设置                    │  ← #8 LLM 配置
└─────────────────────────────┘
```

"创建角色"移至顶栏 "+" 或空状态 CTA（不下沉到侧边栏导航项）。

---

### 验证清单

| # | 检查项 | 验证方法 |
|---|--------|----------|
| 1 | `/status` 显示角色人格/状态/记忆/对话/成长 | 手动点击导航"角色状态" |
| 2 | 人格进度条 6 维均有数据且按值降序 | 截图比对 Vue 版 |
| 3 | 角色选择下拉框工作正常 | 切换角色，数据刷新 |
| 4 | 记忆筛选（对话/事件/成长）正常 | 点击 tab 切换 |
| 5 | 删除角色二次确认后执行 | 点删除 → 确认 → 页面跳转 `/chat` |
| 6 | `/logs` 新增"系统概述"Tab 可切换 | 点导航"系统" → 切到"系统概述" |
| 7 | 系统概述 Tab 显示 MetricCard × 4 | 确认角色总数/活跃会话/平均响应/缓存命中 |
| 8 | 系统概述 Tab 显示响应时间趋势图 | 确认 AreaChart 渲染 |
| 9 | 系统概述 Tab 显示 LLM 服务 + 缓存统计 | 确认 provider/model/latency 显示 |
| 10 | 导航标题准确：角色状态 / 系统 | 检查侧边栏文字 |
| 11 | 旧 StatusPage CSS 类不冲突 | agent-browser 检查所有页面无样式错乱 |
| 12 | 响应式：窄屏双列变单列 | 窗口缩到 900px 以下验证 |
| 13 | 日志浏览 Tab 原有功能不受影响 | 筛选/搜索/详情展开 |
| 14 | 空状态：无角色时显示引导 | 清除 localStorage 后访问 `/status` |
