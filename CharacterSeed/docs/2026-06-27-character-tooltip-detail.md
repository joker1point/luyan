# 2026-06-27 角色头像 Hover 六维面板 + 详情页

## 需求

1. 鼠标悬停侧栏角色头像时，弹出角色六维人格面板
2. 面板下方增加角色简介和"查看详细设定"超链接（跳转到角色详情页）

## 改动文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `web/react-vite/src/components/CharacterStatsTooltip.jsx` | 新建 → 修改 | 六维人格 tooltip 组件，含简介和详情页链接 |
| `web/react-vite/src/pages/CharacterDetailPage.jsx` | 新建 | 角色详情页（雷达图 + 人格六维 + 当前状态） |
| `web/react-vite/src/components/SessionPanel.jsx` | 修改 | 头像外层包裹 `character-avatar-wrap`，添加 hover 状态 |
| `web/react-vite/src/styles.css` | 修改 | tooltip 样式 + 简介/链接样式 |
| `web/react-vite/src/utils/realApi.js` | 修改 | 新增 `getCharacter(id)` 方法 |
| `web/react-vite/src/router/routes.js` | 修改 | 新增 `/character/:characterId` 路由（showInNav: false） |
| `web/react-vite/src/router/lazyPages.js` | 修改 | 新增 `characterDetail` lazy 入口 |
| `web/react-vite/src/router/index.jsx` | 修改 | 新增 NON_NAV_ROUTES 渲染逻辑 |

## 踩坑与解决方案

### 坑 1：tooltip 的 pointer-events 导致链接不可点击

**现象**：tooltip 设置了 `pointer-events: none`（防止 tooltip 遮挡导致鼠标离开 avatar 时 tooltip 不消失），但底部的"查看详细设定"链接无法点击。

**解决**：将 tooltip 的 `pointer-events` 改为 `auto`。hover 消失逻辑由 `onMouseLeave` 在 `character-avatar-wrap` 上控制，tooltip 本身在 wrap 内部，鼠标移到 tooltip 上不会触发 wrap 的 leave（因为 tooltip 是 wrap 的子元素）。

**教训**：`pointer-events: none` 会阻止所有子元素的鼠标事件。如果 tooltip 内有可交互元素（链接/按钮），必须设为 `auto`。hover 消失的正确做法是在**父容器**上监听 `onMouseLeave`，而不是依赖 CSS pointer-events。

### 坑 2：路由表只有 NAV_ROUTES，非导航页面无法渲染

**现象**：`router/index.jsx` 只遍历 `NAV_ROUTES`（`showInNav: true`），角色详情页 `showInNav: false` 导致路由匹配不到，直接 fallback 到 404。

**解决**：新增 `NON_NAV_ROUTES` 过滤（`!r.showInNav && r.path !== '*'`），在 `<Route element={<App />}>` 内额外遍历渲染。

**教训**：路由表中的 `showInNav` 字段原本只控制 NavBar 显示，但 `index.jsx` 的渲染逻辑也依赖它。新增非导航页面时，必须同时修改路由渲染逻辑。

### 坑 3：CharacterStatsTooltip 需要 react-router-dom 的 Link 组件

**现象**：tooltip 组件原本不依赖路由，新增详情页链接后需要 `Link` 组件。由于 tooltip 在 `SessionPanel` 中使用，而 `SessionPanel` 在 `App` 的 `Outlet` 内渲染，`BrowserRouter` 已经包裹了整个应用，所以 `Link` 可以正常工作。

**教训**：在深层组件中使用 `Link` 时，需确认祖先组件树中有 `BrowserRouter`。本项目的 `AppRouter` 在最顶层包裹了 `BrowserRouter`，所以所有子组件都可以安全使用 `Link`。

### 坑 4：character 对象的 description 字段来源

**现象**：`useCharacters.js` 的 `refresh()` 通过 `...c` 展开后端返回的完整对象，所以 `description` 字段已经在 character 对象中。但前端 `tagline` 字段只在 `add()` 本地创建时设置，后端列表不返回 `tagline`。

**解决**：tooltip 中用 `character?.description || character?.tagline || ''` 兜底，优先取后端 `description`，本地创建的角色取 `tagline`。

**教训**：前端 character 对象有两种来源（后端 refresh vs 本地 add），字段不完全一致。访问可选字段时务必用 `?.` 和 `||` 兜底。

## 架构决策

1. **tooltip 用纯 CSS 进度条而非 recharts 雷达图**：`PersonalityRadar` 依赖 recharts（432KB chunk），tooltip 需要轻量快速渲染，所以用 6 个彩色进度条代替。
2. **详情页走独立路由而非 Modal**：角色详情内容较多（雷达图 + 人格 + 状态 + 灵魂设定），用独立页面更合适，也便于未来扩展编辑功能。
3. **NON_NAV_ROUTES 与 NAV_ROUTES 分离渲染**：保持路由表单一数据源，通过 `showInNav` 字段区分，避免新增路由时忘记在多处添加。
