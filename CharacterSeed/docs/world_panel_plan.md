# World 面板完整改造计划

> 审计日期：2026-06-30 | 页面：WorldPage.jsx（678行） | 后端端点：28个 | 前端API：27个
> 核心问题：后端功能完备但前端严重欠开发 —— 10个端点有API函数但WorldPage从未调用，~40个CSS类无样式定义，所有输入用prompt()

---

## 一、现状审计结论

### 1.1 数据富足 vs UI 贫瘠

| 维度 | 后端 | 前端使用率 |
|------|:--:|:--:|
| API 端点 | **28** 个全部实现 | 17个被调用（60%） |
| World CRUD | ✅ 创建/读取/更新/删除 | ❌ 仅读取+部分创建 |
| Location 24属性 | ✅ name/kind/climate/biome_json/capacity/parent_id/is_public/owner_id/description | ❌ 仅显示 name/kind/climate，树形未展示 |
| Item 完整编辑 | ✅ patchItem(PATCH) | ❌ 未调用 |
| Relationship 编辑 | ✅ patchRelationship(PATCH) | ❌ 未调用 |
| 多世界管理 | ✅ World CRUD | ❌ worldId 硬编码为 1 |
| 地点树形 | ✅ parent_id 嵌套 | ❌ 扁平列表 |

### 1.2 交互质量评估

| 交互方式 | 严重度 | 影响范围 |
|----------|:------:|---------|
| `window.prompt()` 做数据输入 | **严重** | 创建地点×3个prompt / 物品×3 / 关系（单prompt） |
| 无编辑Modal/Form | **严重** | 地点/物品/关系只能创建和删除，无法修改 |
| 创建无验证 | **严重** | 可选字段（kind/climate/parent/owner_kind等）完全无法输入 |
| Select下拉无样式 | 高 | `.select`类无CSS定义 |
| 无确认对话框（删除需要） | 高 | 误点即删，不可逆 |

### 1.3 CSS 灾难

~40个世界专属CSS类中，**只有关系网相关的~200行**（.seg / .rel-graph-card / .broadcast-form / .rel-list）有样式定义。以下核心类全部裸奔：

```
.world-page, .state-card, .state-row, .state-cell, .state-label, .state-value,
.state-actions, .weather-grid, .weather-cell, .weather-name, .weather-meta,
.event-list, .event-item, .event-title, .event-desc, .event-meta,
.tab-bar, .tab, .tab-content, .loading,
.locations-panel, .panel-toolbar, .loc-list, .loc-item, .loc-name, .loc-path, .loc-meta,
.items-panel, .item-list, .item-row, .item-name, .item-desc, .item-meta,
.rels-panel, .select
```

---

## 二、改造计划

### Phase 1 — 视觉骨架（P0）

> 目标：页面有基本样式，可读可用。

#### 1.1 新增 CSS 规则

**文件：** `web/react-vite/src/styles.css`

需要新增约 300 行 CSS。关键规则：

```css
/* Tab栏 — 与 tab-switcher 统一风格 */
.tab-bar { display: flex; gap: 2px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.tab { padding: 8px 16px; border: none; background: none; color: var(--text-tertiary);
       font-size: 14px; cursor: pointer; border-bottom: 2px solid transparent;
       transition: color 0.15s, border-color 0.15s; }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }

/* 状态卡 */
.state-card { background: var(--bg-soft); border-radius: var(--radius); padding: 16px; }
.state-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.state-cell { text-align: center; }
.state-label { font-size: 11px; color: var(--text-tertiary); margin-bottom: 4px; }
.state-value { font-size: 18px; font-weight: 600; }

/* 操作按钮行 */
.state-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }

/* 天气网格 */
.weather-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin-top: 16px; }
.weather-cell { background: var(--bg-soft); border-radius: var(--radius); padding: 10px; text-align: center; }
.weather-name { font-size: 13px; font-weight: 600; }
.weather-meta { font-size: 11px; color: var(--text-tertiary); margin-top: 2px; }

/* 事件列表 */
.event-list { display: flex; flex-direction: column; gap: 6px; max-height: 300px; overflow-y: auto; }
.event-item { background: var(--bg-soft); border-radius: var(--radius-sm); padding: 8px 12px; }
.event-title { font-size: 13px; font-weight: 600; }
.event-desc { font-size: 12px; color: var(--text-secondary); }
.event-meta { font-size: 11px; color: var(--text-tertiary); margin-top: 4px; }

/* 面板工具栏 */
.panel-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }

/* 地点树形列表 */
.loc-list { display: flex; flex-direction: column; gap: 4px; }
.loc-item { display: flex; align-items: center; justify-content: space-between;
            padding: 8px 12px; background: var(--bg-soft); border-radius: var(--radius-sm); }
.loc-name { font-size: 14px; font-weight: 600; }
.loc-path { font-size: 11px; color: var(--text-tertiary); margin-left: 8px; }
.loc-meta { display: flex; gap: 6px; align-items: center; }

/* 物品列表 */
.item-list { display: flex; flex-direction: column; gap: 4px; }
.item-row { display: flex; align-items: center; justify-content: space-between;
            padding: 8px 12px; background: var(--bg-soft); border-radius: var(--radius-sm); }
.item-name { font-size: 14px; font-weight: 600; }
.item-desc { font-size: 12px; color: var(--text-secondary); max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item-meta { display: flex; gap: 6px; align-items: center; font-size: 12px; }

/* 加载 */
.loading { text-align: center; padding: 40px; color: var(--text-tertiary); }

/* Select 下拉 */
.select { background: var(--bg-soft); border: 1px solid var(--border); border-radius: var(--radius-sm);
          color: var(--text); padding: 6px 10px; font-size: 13px; }
```

#### 1.2 Tab 栏视觉统一

**文件：** `WorldPage.jsx`

将硬编码的 `<div className="tab-bar">` 改为复用项目已有的 `.app-tab-bar` 风格。当前项目 ChatPage / CreatePage 已使用类似的 tab 切换器（`.tab-switcher`），WorldPage 应与之保持一致。

---

### Phase 2 — 交互升级（P0）

> 目标：替换 `prompt()`，添加编辑功能，完善创建表单。

#### 2.1 新建通用 Modal 组件

**文件：** `web/react-vite/src/components/Modal.jsx`（新建）

```jsx
export default function Modal({ open, onClose, title, children, footer }) {
  if (!open) return null
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="btn btn-ghost btn-icon" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  )
}
```

#### 2.2 新建独立的子面板组件

将 WorldPage 内部 4 个内联面板组件提取为独立文件：

| 新建文件 | 提取自 | 功能 |
|---------|--------|------|
| `web/react-vite/src/components/panels/WorldStatePanel.jsx` | WorldPage.jsx L49-267 | 世界状态+天气+事件 |
| `web/react-vite/src/components/panels/LocationsPanel.jsx` | WorldPage.jsx L269-344 | 地点树形CRUD |
| `web/react-vite/src/components/panels/ItemsPanel.jsx` | WorldPage.jsx L346-433 | 物品CRUD+过滤 |
| `web/react-vite/src/components/panels/RelationshipsPanel.jsx` | WorldPage.jsx L435-634 | 关系CRUD+网图 |

WorldPage.jsx 瘦身为 ~50 行的 Tab 路由容器。

#### 2.3 替换 prompt() 为真实表单

**地点创建/编辑 Modal 表单：**

| 字段 | 组件 | 必填 | 说明 |
|------|------|:--:|------|
| name | `<input>` | ✅ | 地点名称 |
| kind | `<select>` | ✅ | city / building / room / landscape / dungeon / generic |
| climate | `<select>` | ❌ | tropical / temperate / arctic / desert / aquatic / underground |
| parent_id | `<select>`(地点树列表) | ❌ | 父地点选择（支持嵌套） |
| description | `<textarea>` | ❌ | 地点描述 |
| capacity | `<input type="number">` | ❌ | 容量 |
| is_public | `<input type="checkbox">` | ❌ | 是否公开 |

**物品创建/编辑 Modal 表单：**

| 字段 | 组件 | 必填 | 说明 |
|------|------|:--:|------|
| name | `<input>` | ✅ | 物品名称 |
| description | `<textarea>` | ❌ | 描述 |
| owner_kind | `<select>` | ✅ | character / location / container |
| owner_id | `<input type="number">` | ✅ | 拥有者ID |
| rarity | `<input type="number" min="0" max="10">` | ❌ | 稀有度 |
| value | `<input type="number">` | ❌ | 价值 |

**关系创建/编辑 Modal 表单：**

| 字段 | 组件 | 必填 | 说明 |
|------|------|:--:|------|
| char_a_id | `<select>`(角色列表) | ✅ | 角色A |
| char_b_id | `<select>`(角色列表) | ✅ | 角色B（排除A） |
| type | `<select>` | ✅ | family / friend / lover / rival / mentor / acquaintance / enemy |
| strength | `<input type="range" min="-100" max="100">` | ❌ | 强度滑条 |

#### 2.4 添加编辑/删除确认

每个列表项的右侧增加：
- ✏️ 编辑按钮 → 打开 Modal（表单预填当前值），调用 patchXxx
- 🗑 删除按钮 → 弹出确认对话框 → 调用 deleteXxx
- ✅ 删除确认可复用 `window.confirm()` 或升级为自定义 ConfirmDialog

---

### Phase 3 — 功能补全（P1）

> 目标：激活后端已有但前端未使用的功能。

#### 3.1 多世界支持

**改动：** WorldPage 顶部添加 `<select>` 世界切换器

```
[ 默认世界 ▼ ]  [+ 新建世界]
```

- 调用 `listWorlds()` → 填充下拉
- 创建世界 Modal → 输入名称/描述/rules_json → POST /api/worlds
- 选中世界后更新所有 Tab 的 `worldId`
- 移除 `DEFAULT_WORLD_ID = 1` 硬编码

#### 3.2 地点树形展示

**改动：** LocationsPanel 中按 `parent_id` 构建树形结构

后端 `GET /api/worlds/{wid}/locations` 返回的每条 Location 已包含 `parent_id` 和 `path` 字段。前端只需：

```jsx
function buildTree(locations) {
  const map = {}
  const roots = []
  locations.forEach(loc => { map[loc.id] = { ...loc, children: [] } })
  locations.forEach(loc => {
    if (loc.parent_id && map[loc.parent_id]) {
      map[loc.parent_id].children.push(map[loc.id])
    } else {
      roots.push(map[loc.id])
    }
  })
  return roots
}

function renderTree(nodes, depth = 0) {
  return nodes.map(node => (
    <div key={node.id} style={{ marginLeft: depth * 20 }}>
      <div className="loc-item">{/* ... */}</div>
      {renderTree(node.children, depth + 1)}
    </div>
  ))
}
```

#### 3.3 角色世界上下文展示

**新增区域：** 在 WorldStatePanel 底部（或在新的 Tab "上下文"中）

调用 `GET /api/characters/{cid}/world-context`，显示：
- 角色所在位置（Location path + kind + description）
- 当前位置天气
- 世界背景（world_setting）
- 附近地点（siblings）
- 最近世界事件

通过角色下拉选择器切换角色。

#### 3.4 关系强度滑条 + 可视化

**改动：** RelationshipsPanel 中

- `patchRelationship` 作为编辑入口，支持修改 type + strength
- 在网图上用 `strength` 决定连线粗细/颜色
- 颜色映射：-100~-30=红色(敌意), -30~30=灰色(中立), 30~100=绿色(友好)

---

### Phase 4 — 视觉增强（P2）

> 目标：信息密度提升，视觉美观。

#### 4.1 世界事件时间线

在 WorldStatePanel 中添加可水平滚动的时间线：

```
Day 60 ─── 春分 ─── Day 151 ─── 夏至 ─── Day 159 ─── 现在 ─── Day 242 ─── 秋分
  🌱          🌤️           ☀️         🌞           📍           🍂
```

#### 4.2 天气渐进式图标

为天气 cell 增加动态效果（CSS transition），鼠标悬停显示未来 7 天预测。

#### 4.3 角色位置热力图

在地点树形图上叠加"谁在哪"标签，用 `characters` 列表的 `current_location_id` 匹配 locations。

#### 4.4 季节背景色

根据 `season` 为 WorldStatePanel 的 `.state-card` 添加季节色背景：
- 春：`background: linear-gradient(135deg, #f0fdf4, #dbeafe)`
- 夏：`background: linear-gradient(135deg, #fef3c7, #fed7aa)`
- 秋：`background: linear-gradient(135deg, #fefce8, #fde68a)`
- 冬：`background: linear-gradient(135deg, #f0f9ff, #e0e7ff)`

---

## 三、完整改动清单

| 序号 | 文件 | 操作 | 内容 |
|:--:|------|:--:|------|
| P0-1 | `web/react-vite/src/styles.css` | 修改 | 新增 ~300 行世界相关 CSS |
| P0-2 | `web/react-vite/src/components/Modal.jsx` | **新建** | 通用 Modal 组件 |
| P0-3 | `web/react-vite/src/components/panels/WorldStatePanel.jsx` | **新建** | 世界状态子面板 |
| P0-4 | `web/react-vite/src/components/panels/LocationsPanel.jsx` | **新建** | 地点CRUD子面板（含编辑Modal+确认删除） |
| P0-5 | `web/react-vite/src/components/panels/ItemsPanel.jsx` | **新建** | 物品CRUD子面板 |
| P0-6 | `web/react-vite/src/components/panels/RelationshipsPanel.jsx` | **新建** | 关系CRUD子面板（含编辑Modal） |
| P0-7 | `web/react-vite/src/pages/WorldPage.jsx` | 修改 | 瘦身为 ~50 行 Tab 容器，导入子面板 |
| P0-8 | `web/react-vite/src/components/RelationshipGraph.jsx` | 修改 | 连线粗细 + 颜色 按 strength 映射 |
| P1-1 | `web/react-vite/src/pages/WorldPage.jsx` | 修改 | 世界选择器替代硬编码 worldId=1 |
| P1-2 | (WorldPage.jsx 内) | 修改 | 地点树形展示 |
| P1-3 | (WorldPage.jsx 内) | 新增 | 角色世界上下文展示区域 |
| P1-4 | `web/react-vite/src/components/panels/RelationshipsPanel.jsx` | 修改 | 强度滑条编辑 |
| P2-1 | `WorldStatePanel.jsx` | 新增 | 世界事件时间线 |
| P2-2 | `WorldStatePanel.jsx` | 修改 | 季节背景色 |
| P2-3 | `LocationsPanel.jsx` | 新增 | 角色位置叠加标注 |

---

## 四、分阶段执行顺序

```
Phase 1 — 视觉骨架（1h）
  ├── P0-1: CSS 规则
  └── P0-7: Tab 栏视觉统一

Phase 2 — 交互升级（2h）
  ├── P0-2: Modal 组件
  ├── P0-3~6: 提取 4 个子面板（带编辑 Modal + 确认删除）
  ├── P0-7: WorldPage 瘦身
  └── P0-8: 关系网强度可视化

Phase 3 — 功能补全（1.5h）
  ├── P1-1: 多世界切换
  ├── P1-2: 地点树形
  ├── P1-3: 角色世界上下文
  └── P1-4: 关系编辑滑条

Phase 4 — 视觉增强（1h）
  ├── P2-1: 事件时间线
  ├── P2-2: 季节背景色
  └── P2-3: 角色位置标注
```

---

## 五、验收标准

| # | 测试项 | 步骤 | 预期结果 |
|:--:|--------|------|---------|
| 1 | CSS 完整性 | 打开 World 页 | 所有区域有间距/边框/圆角/颜色，无裸奔元素 |
| 2 | 地点创建 | 点击"新建地点" → 弹出 Modal → 填写 5 个字段 → 提交 | 地点列表新增，显示 kind/climate chip |
| 3 | 地点编辑 | 点击编辑按钮 → Modal 预填当前值 → 修改 climate → 提交 | 列表更新 |
| 4 | 地点删除 | 点击删除 → 确认对话框 → 确认 | 列表减少 |
| 5 | 物品创建/编辑/删除 | 同上 | 同上 |
| 6 | 关系创建/编辑/删除 | 同上 | 同上（编辑支持 type 下拉 + strength 滑条） |
| 7 | 地点树形 | 创建子地点（parent_id=某地点） | 列表中按层级缩进展示父子关系 |
| 8 | 世界切换 | 创建第二个世界 → 下拉切换 | 4 个 Tab 内容更新为新世界数据 |
| 9 | 关系网强度着色 | 关系 strength=100 vs -100 | 绿色粗线 vs 红色细线 |
| 10 | 操作反馈 | 创建/编辑/删除后 | Toast 提示成功或失败 |
| 11 | 空状态 | 新世界无任何地点/物品/关系 | 显示 EmptyState 占位，引导创建 |
