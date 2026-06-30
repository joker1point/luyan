# 角色创建 4 步流程修复计划

> **审计日期**: 2026-06-30
> **范围**: `CreatePage.jsx` + `realApi.js` + `character_router.py` + `creation.py`
> **排除**: 文件上传模式（安全风险，后续单独处理）

---

## 修复总览

| # | 问题 | 严重度 | 改动文件 | 预估行数 |
|---|------|--------|----------|----------|
| F1 | 确认创建时触发生图（移入 Step 4） | 🔴 高 | CreatePage.jsx | ~40 行 |
| F2 | Name 未送达后端 | 🔴 高 | realApi.js + character_router.py | ~8 行 |
| F3 | Dimensions 全部丢弃 | 🔴 高 | realApi.js + creation.py | ~15 行 |
| F4 | 创建按钮缺少 loading 状态 | 🔴 高 | CreatePage.jsx | ~5 行 |
| F5 | AI 润色 AbortController 断线 | ⚠️ 中 | realApi.js | ~3 行 |

**总计**: ~70 行改动，4 个文件。

---

## F1: 确认创建时触发生图（核心需求）

### 当前流程（有缺陷）

```
Step 4 点"创建角色"
    → handleCreate() → addCharacter()
        → api.createCharacter()  ← 只发 description
        → setCreatedChar() → setShowSuccess(true)
            ↓ 跳转到 CreateSuccessPage
            ↓ useEffect 自动调 generateAvatar()   ← 太晚了！用户已经看到成功页
```

### 目标流程

```
Step 4 点"创建角色"
    → handleCreate()
        → [1] 按钮进入 loading（禁用，显示 spinner）
        → [2] addCharacter() → api.createCharacter()  ← 带 name + dimensions
        → [3] 立即调用 api.generateAvatar(cid)       ← 创建完立刻生图
        → [4] setCreatedChar(含 avatarGenerating=true)
        → [5] setShowSuccess(true)                    ← 成功页已在轮询候选图
```

### 改动文件: `CreatePage.jsx`

#### 改动 1.1 — handleCreate 增加 avatar 触发逻辑

**位置**: L127-152 (`handleCreate` 函数)

**当前代码**:
```js
const handleCreate = async () => {
  let newChar = null
  try {
    newChar = await addCharacter({
      name: form.name,
      description: form.description,
      personalityText: form.personalityText,
    })
    setCreatedChar(newChar)
    setShowSuccess(true)
    return newChar
  } catch (e) { ... }
}
```

**改为**:
```js
const handleCreate = async () => {
  let newChar = null
  try {
    // [F4] 设置创建中状态（防止重复点击）
    setCreating(true)

    // [F2+F3] 创建角色（name + dimensions 已通过 addCharacter 内部传递）
    newChar = await addCharacter({
      name: form.name,
      description: form.description,
      personalityText: form.personalityText,
      dimensions: form.dimensions,          // [F3 新增]
    })

    // [F1] 创建成功后立即触发头像生成（不等 success page 的 useEffect）
    const cid = backendIdForAvatar(newChar.id)
    if (cid != null && api) {
      try {
        await api.generateAvatar(cid, {
          style: 'anime',
          expression: 'neutral',
          background: 'simple',
        })
        // 标记头像已提交生成任务，success page 会接手轮询
        newChar._avatarSubmitted = true
      } catch (_) {
        // 头像任务提交失败不阻塞创建流程
        newChar._avatarSubmitted = false
      }
    }

    setCreatedChar(newChar)
    setShowSuccess(true)
    return newChar
  } catch (e) {
    const msg = (e && (e.detail || e.message)) || '创建失败'
    toast.error(msg)
    setErrors(prev => {
      const next = { ...prev }
      if (next.name) delete next.name
      if (next.description) delete next.description
      if (next.personalityText) delete next.personalityText
      return next
    })
    throw e
  } finally {
    setCreating(false)  // [F4] 无论成功失败都恢复按钮
  }
}
```

#### 改动 1.2 — 新增 state 和辅助函数

**位置**: L76-85（state 声明区）

**新增**:
```js
// [F4] 创建中状态（按钮 loading）
const [creating, setCreating] = useState(false)

// [F1 辅助] 从 frontend char id 提取 backend int id
const backendIdForAvatar = (fid) => {
  if (!fid) return null
  if (typeof fid === 'string' && fid.startsWith('char-')) {
    return parseInt(fid.slice(5), 10) || null
  }
  return parseInt(fid, 10) || null
}
```

注意：`backendIdRef` 已经存在于 `CreateSuccessPage` 组件内（L654-666），但那是成功页的局部 ref。`handleCreate` 在外层组件，需要一个独立版本或把 ref 提升到父级。**方案：直接在 handleCreate 里内联提取，不需要额外 ref。**

#### 改动 1.3 — 创建按钮加 loading

**位置**: L576-581（Step 4 的"创建角色"按钮）

**当前代码**:
```jsx
{isLastStep && (
  <button className="btn btn-primary" onClick={handleCreate}>
    <Sparkles size={16} />
    创建角色
  </button>
)}
```

**改为**:
```jsx
{isLastStep && (
  <button
    className="btn btn-primary"
    onClick={handleCreate}
    disabled={creating}                     // [F4] 防重复点击
  >
    {creating ? (
      <>
        <Loader2 size={16} className="spin-icon" />
        创建中...
      </>
    ) : (
      <>
        <Sparkles size={16} />
        创建角色
      </>
    )}
  </button>
)}
```

#### 改动 1.4 — CreateSuccessPage 接收 _avatarSubmitted 标记

**位置**: `CreateSuccessPage` 组件（L635 起）

**当前**: `useEffect` 无条件自动调 `generateAvatar`（L668-724）

**改为**: 如果 `_avatarSubmitted === true` 则跳过自动提交，只做轮询：

```js
// [F1] 如果 handleCreate 已经提交了头像生成任务，跳过重复提交
const avatarPreSubmitted = !!character._avatarSubmitted

useEffect(() => {
  if (!api || !backendId.current) return

  // [F1] 已预提交 → 直接开始轮询，不再调 generateAvatar
  if (avatarPreSubmitted) {
    startPollingOnly()
    return
  }

  // 未预提交 → 原有逻辑：先提交再轮询
  startGenerateThenPoll()
  // ...原有代码不变...
}, [api, backendId.current])
```

### 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| generateAvatar 提交失败但角色已创建 | 低 | 中 | catch 不抛错，success page 显示手动重试入口 |
| 用户快速点两次创建 | 中 | 低 | disabled+creating 双重防护 |
| 后端返回 id 格式异常 | 极低 | 低 | backendIdForAvatar 有 parseInt 兜底 |

---

## F2: Name 未送达后端

### 问题根因

```
前端:  api.createCharacter({ name, description, personalityText })
         ↓
realApi.js:606  const userInput = [description, personalityText].join('\n\n')
               fd.append('description', userInput)   // ← 只有 description！name 丢了
         ↓
后端:  create_character(description=..., story_file=None)
         ↓ user_input = description
         ↓ LLM 从文本中猜名字（可能猜出不同名字）
```

### 改动文件: `realApi.js` + `character_router.py`

#### 改动 2.1 — realApi.js 发送 name 字段

**位置**: `realApi.js` L606-626 (`createCharacter` 方法)

**当前代码**:
```js
async createCharacter({ name, description, personalityText } = {}) {
  const base = resolveBase()
  const userInput = [description, personalityText].filter(Boolean).join('\n\n') || (name || '').trim()
  const fd = new FormData()
  fd.append('description', userInput)
  ...
}
```

**改为**:
```js
async createCharacter({ name, description, personalityText, dimensions } = {}) {
  const base = resolveBase()
  const safeName = (name || '').trim()

  // [F2] 把描述和性格拼成 userInput（保持原有语义）
  const userInput = [description, personalityText].filter(Boolean).join('\n\n') || safeName
  const fd = new FormData()

  // [F2] name 作为独立字段传给后端
  fd.append('name', safeName)
  fd.append('description', userInput)

  // [F3] dimensions 作为 JSON 字符串传给后端
  if (dimensions && typeof dimensions === 'object') {
    fd.append('dimensions', JSON.stringify(dimensions))
  }

  const resp = await fetch(`${base}/api/characters/create`, {
    method: 'POST',
    body: fd,
  })
  ...其余不变
}
```

#### 改动 2.2 — character_router.py 接收 name 字段

**位置**: `character_router.py` L50-68 (`create_character` 端点)

**当前代码**:
```python
@router.post("/api/characters/create", response_model=CharacterResponse)
async def create_character(
    description: Optional[str] = Form(None),
    story_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
```

**改为**:
```python
@router.post("/api/characters/create", response_model=CharacterResponse)
async def create_character(
    description: Optional[str] = Form(None),
    story_file: Optional[UploadFile] = File(None),
    name: Optional[str] = Form(None),           # [F2 新增]
    dimensions: Optional[str] = Form(None),     # [F3 新增] JSON 字符串
    db: Session = Depends(get_db),
):
    """创建角色（支持一句话描述或TXT文件上传）。"""
    # ...原有 story_file / description 逻辑不变...

    try:
        parsed_data, raw_response = get_creation_module().run(
            user_input, input_type,
            preferred_name=name,           # [F2 新增]
            dimensions_hint=dimensions,    # [F3 新增]
        )

        # [F2] 如果用户提供了 name 且 LLM 返回的名字差异过大，优先使用用户名
        llm_name = parsed_data.get("name", "未命名角色")
        final_name = name.strip() if (name and name.strip()) else llm_name
        ...

        db_character = character_crud.create_character(
            db=db,
            name=final_name,              # 使用最终确定的名字
            ...
        )
```

---

## F3: Dimensions 全部丢弃

### 问题根因

```
Step 3: 用户调了 6 维滑块 → form.dimensions = { optimism:48, courage:83, ... }
Step 4: handleCreate({ name, description, personalityText })  ← 没有 dimensions！
api.createCharacter() → FormData 只有 description             ← dimensions 不存在
后端 CreationModule.run(user_input) → LLM 自己猜维度          ← 用户设值完全丢失
```

### 设计决策：Dimensions 如何影响 LLM？

**方案 A（推荐）**: 将 dimensions 作为"偏好提示"注入 prompt，LLM 参考但不强制覆盖

- 在 `creation.txt` prompt 模板末尾追加一段条件提示：
  ```
  {dimensions_hint}
  ```
  当 `dimensions_hint` 非空时展开为：
  ```
  【用户设定的性格倾向参考】
  - 乐观度: 48/100 （偏中性）
  - 勇气: 83/100 （偏高）
  ...
  请在生成 personality 时参考以上数值，让生成的属性与用户的期望尽量一致。
  ```

- **优点**: 不破坏现有 prompt 结构；LLM 仍有创作空间；向后兼容（无 hint 时行为不变）
- **缺点**: LLM 可能不完全遵循数值

**方案 B（备选）**: 直接用用户值覆盖 LLM 生成的 personality 字段

- 在 `parse_response()` 之后，如果 `dimensions_hint` 存在，直接 `parsed_data["personality"] = json.loads(dimensions_hint)`
- **优点**: 用户设值 100% 生效
- **缺点**: personality 与 speaking_style/values/habits 可能不一致（如勇气=95 但说话风格写"胆小谨慎"）

**推荐方案 A**，给 LLM 创作自由度同时尊重用户意图。

### 改动文件: `creation.py` + `prompts/creation.txt`

#### 改动 3.1 — CreationModule.run() 接受新参数

**位置**: `creation.py` L93-116 (`run` 方法)

**当前签名**:
```python
def run(self, user_input: str, input_type: str = "text") -> tuple[Dict[str, Any], str]:
```

**改为**:
```python
def run(
    self,
    user_input: str,
    input_type: str = "text",
    preferred_name: Optional[str] = None,      # [F2]
    dimensions_hint: Optional[str] = None,    # [F3] JSON string
) -> tuple[Dict[str, Any], str]:
```

**run() 内部新增** (在 `build_prompt` 之前):

```python
# [F3] 构建 dimensions 提示文本
dimensions_text = ""
if dimensions_hint:
    try:
        dims = json.loads(dimensions_hint)
        if isinstance(dims, dict):
            labels = {
                "optimism": "乐观度", "courage": "勇气",
                "empathy": "同理心", "loyalty": "忠诚度",
                "intelligence": "智力", "sociability": "社交性",
            }
            lines = []
            for key, label in labels.items():
                val = dims.get(key)
                if isinstance(val, (int, float)):
                    level = "极高" if val >= 80 else ("偏高" if val >= 60 else ("中等" if val >= 40 else ("偏低" if val >= 20 else "极低")))
                    lines.append(f"- {label}: {val}/100 （{level}）")
            if lines:
                dimensions_text = "\n【用户设定的性格倾向参考】\n" + "\n".join(lines) + "\n请在生成 personality 时参考以上数值，让生成的属性与用户的期望尽量一致。\n"
    except (json.JSONDecodeError, TypeError):
        pass  # 忽略非法 JSON，静默降级
```

#### 改动 3.2 — build_prompt 注入 dimensions

**位置**: `creation.py` L38-52 (`build_prompt` 方法)

**当前**:
```python
def build_prompt(self, validated_input: str) -> str:
    prompt = self.prompt_template.replace("{user_description}", validated_input)
    return prompt
```

**改为**:
```python
def build_prompt(self, validated_input: str, dimensions_text: str = "") -> str:
    prompt = self.prompt_template.replace("{user_description}", validated_input)
    # [F3] 追加 dimensions 提示（如果有）
    if dimensions_text:
        prompt = prompt.rstrip() + "\n\n" + dimensions_text
    return prompt
```

**并在 `run()` 中传入**:

```python
prompt = self.build_prompt(validated_input, dimensions_text=dimensions_text)
```

#### 改动 3.3 — preferred_name 注入 prompt（可选增强）

在 `run()` 方法中，如果 `preferred_name` 存在，也在 prompt 末尾追加上一句：

```python
name_hint = ""
if preferred_name and preferred_name.strip():
    name_hint = f"\n【用户指定的角色名称】: {preferred_name.strip()}\n请使用此名称作为角色的正式名字。\n"

# 合并到 prompt
if name_hint:
    prompt = prompt.rstrip() + "\n\n" + name_hint
```

这样 LLM 生成的 `name` 字段就会直接使用用户填的名字。

---

## F4: 创建按钮 Loading 状态

> 已在 F1 的改动 1.2 和 1.3 中一并实现。此处为补充说明。

### 改动清单

| 位置 | 改动 |
|------|------|
| state 区 | `const [creating, setCreating] = useState(false)` |
| handleCreate | 入口 `setCreating(true)`，finally `setCreating(false)` |
| 按钮 JSX | `disabled={creating}` + 条件渲染 `<Loader2>` / `<Sparkles>` |
| CSS | `.spin-icon { animation: spin 1s linear infinite; }`（约 3 行） |

### CSS 补充（styles.css 末尾追加）

```css
/* [F4] 创建按钮 spinner 动画 */
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.spin-icon {
  animation: spin 1s linear inline;
}
```

---

## F5: AI 润色 AbortController 断线

### 问题根因

```
CreatePage:  ctrl = new AbortController()
             api.polishDescription({ description, signal: ctrl.signal })  ← signal 传入了
                      ↓
realApi.js:672  async polishDescription({ description, signal } = {}) {
                 _fetchWithTimeout('/...', {
                   method: 'POST',
                   body: { ... },
                   ...(signal ? { headers: {}, /* 透传 signal */ } : {} ),
                                                      ↑ 这里写了注释但实际没传！
                 })
```

`_fetchWithTimeout` 的第二个参数是一个 options 对象，其中没有 `signal` 字段被显式传入。虽然 `_fetchWithTimeout` 内部创建了 `AbortController`，但它只检查外部 `signal` 参数——而这里的 spread `{ headers: {} }` 并没有包含 `signal`。

### 改动: `realApi.js` L672-686

**当前代码**:
```js
async polishDescription({ description, signal } = {}) {
    const data = await _fetchWithTimeout(
      '/api/characters/polish-description',
      {
        method: 'POST',
        body: { description: String(description || '') },
        timeoutMs: 60000,
        ...(signal ? { headers: {}, /* 透传 signal 见 _fetchWithTimeout */ } : {}),
      },
    )
    return { polished: ..., original: ... }
},
```

**改为**:
```js
async polishDescription({ description, signal } = {}) {
    const data = await _fetchWithTimeout(
      '/api/characters/polish-description',
      {
        method: 'POST',
        body: { description: String(description || '') },
        timeoutMs: 60000,
        signal: signal || null,    // [F5 修复] 显式传入 signal
      },
    )
    return {
      polished: (data && data.polished) || String(description || ''),
      original: (data && data.original) || String(description || ''),
    }
},
```

**验证方式**: 
1. 点"AI 润色" → 立即再点一次（应 abort 上一次并重新发起）
2. Network 面板确认旧请求的 Status 为 `(canceled)`

---

## 执行顺序与依赖关系

```
F5 ───────────────→ 独立，可最先改（3行，零风险）
 │
 ├── F2 ──────────→ 独立，改 realApi.js + character_router.py（8行）
 │
 ├── F3 ──────────→ 依赖 F2（同一个函数），改 creation.py + prompt（15行）
 │
 ├── F4 ──────────→ 独立，改 CreatePage.jsx state + 按钮（8行）
 │
 └── F1 ──────────→ 依赖 F4（共用 creating state），改 handleCreate + SuccessPage（~40行）
                    最后执行，因为要整合前面所有改动
```

建议按 **F5 → F2 → F3 → F4 → F1** 顺序执行，每步可独立验收。

---

## 验收方案

### V1: 单元级（不改文件就能验）

```bash
# 检查 F5: realApi.js 中 polishDescription 是否传 signal
grep -A5 "polishDescription" web/react-vite/src/utils/realApi.js | grep "signal"

# 检查 F2: createCharacter 是否 append name
grep -n "append.*name\|append.*dimensions" web/react-vite/src/utils/realApi.js

# 检查 F4: 创建按钮是否有 disabled
grep -n "disabled.*creating\|setCreating" web/react-vite/src/pages/CreatePage.jsx
```

### V2: 集成级（需要启动后端）

```bash
# 1. 启动后端
cd CharacterSeed && python -m uvicorn backend.main:app --reload --port 8000

# 2. 用 curl 模拟带 name 的创建请求
curl -X POST http://localhost:8000/api/characters/create \
  -F "name=测试角色" \
  -F "description=这是一个测试描述"

# 期望: 返回 JSON 中 name == "测试角色" 或 LLM 基于"测试角色"生成的名字

# 3. 用 curl 模拟带 dimensions 的创建请求
curl -X POST http://localhost:8000/api/characters/create \
  -F "name=勇敢者" \
  -F "description=一个勇敢的战士" \
  -F 'dimensions={"optimism":90,"courage":95,"empathy":30,"loyalty":80,"intelligence":60,"sociability":40}'

# 期望: 返回的 personality 中 courage 接近 90-100（而非默认50附近）
```

### V3: UI 级（需要完整前端）

1. 打开创建页面 → 填写基本信息 → 填写性格定义 → 调整维度到极端值（如勇气=95）
2. 到 Step 4 确认页 → 点「创建角色」
3. **[F4 验收]** 按钮立即变为 `<Loader2> 创建中...` 且不可再次点击
4. **[F2 验收]** 创建成功后角色名 == Step 1 填写的名字（非 LLM 猜测）
5. **[F3 验收]** 进入对话页查看该角色的 Jiwen 配置，courage 维度的初始值/速率偏向高值
6. **[F1 验收]** 成功页出现时头像区域已经在"生成中"（而非空白等 2 秒才开始）
7. **[F5 验收]** Step 1 点"AI 润色" → 润色中再点一次 → 第一次请求被取消

---

## 回滚方案

所有改动都在 git 已提交的快照 `f6f3ab0` 之上：

```bash
git diff f6f3ab0 HEAD --stat          # 查看本次改动文件列表
git checkout f6f3ab0 -- <file_path>    # 回滚单个文件
git reset --hard f6f3ab0               # 完全回滚到修复前
```
