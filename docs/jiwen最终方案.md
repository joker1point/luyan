代码已全部审计完毕。以下是完整决策方案：

***

# 审计报告：创建成功反馈 & 设置/jiwen 硬编码

***

## 一、角色创建后无"创建成功"反馈

### 现状

`CreateView.vue` 第 199-220 行的 `submit()` 函数：

```
创建成功 → result = created（内联预览卡片出现）
         → setActive(created.id)
         → refreshList()
         → ❌ 无 toast/notification
         → ❌ 无自动跳转
```

对比 `SettingsView.vue` 第 278 行用了 `window.alert('已保存')`——粗糙但至少给了反馈。创建角色比保存设置重要得多，却完全没有显式通知。

### 决策

| 阶段       | 决策                                                 | 理由                        |
| :------- | :------------------------------------------------- | :------------------------ |
| **即时反馈** | 顶部 toast 通知："✅ 角色「{name}」创建成功"                     | 比 alert 更现代，3 秒自动消失，不打断流程 |
| **后续引导** | 创建成功后自动跳转到 `/chat` 页面                              | 用户创建角色的目的是聊天，不应停在预览页      |
| **可选方案** | 如果不想自动跳转，至少将"去对话 →"链接改成醒目按钮："🎉 创建成功！去和 {name} 聊天" | 降低认知负荷                    |

**推荐：即时 toast + 自动跳转**。两个一组，不冲突。

***

## 二、设置页和 jiwen 配置硬编码问题

审计发现 **19 处硬编码**，按严重度分 5 个优先级。

***

### P0：前后端不一致（bug）

| 位置                                                   | 问题                                         | 决策                                                                                        |
| :--------------------------------------------------- | :----------------------------------------- | :---------------------------------------------------------------------------------------- |
| `SettingsView.vue:187` `active_provider: 'deepseek'` | 前端默认 DeepSeek，后端 `DEFAULT_ACTIVE = "qwen"` | 前端应从 API 获取默认值，不要自己写。**当前如果后端没有 qwen 的 API key，前端表单加载时 provider 显示 deepseek 但实际后端用 qwen** |

**决策：** `SettingsView.vue` 的 `loadSettings()` 需要从后端拉取 `active_provider` 作为初始值，而不是硬编码 `deepseek`。

***

### P0：Jiwen 核心参数完全硬编码

**文件：** `backend/jiwen/jiwen_core.py`

| 类别                        |  数量  | 当前状态        | 决策                  |
| :------------------------ | :--: | :---------- | :------------------ |
| 漂移率 (`DEFAULT_RATES`)     | 15 个 | 只能代码传参，无 UI | **全部暴露为 API 可配置参数** |
| 阈值 (`DEFAULT_THRESHOLDS`) |  6 个 | 只能代码传参，无 UI | **全部暴露为 API 可配置参数** |
| Activity 白名单              |  5 种 | `set` 硬编码   | **改为从配置表读取，支持扩展**   |
| User Status 白名单           |  4 种 | `set` 硬编码   | **改为从配置表读取，支持扩展**   |

这 21 个参数是角色行为的**核心旋钮**——连接感的漂移速度、什么时候角色会主动联系用户、哪些活动影响沉浸度，全部写死在代码里。用户现在看到的设置页是"假设置"——表单里的值有 UI，但真正影响行为的值改不了。

**决策：**

1. **新增 API 端点：** `GET/PUT /api/jiwen/characters/{id}/params` — 暴露漂移率 + 阈值 + activity 映射
2. **前端新增 Jiwen 参数面板（Settings 页的子 Tab）：** 漂移率用 slider（0.0–1.0），阈值用 slider + 分段中文说明
3. **后端 JiwenState 模型新增** **`config_json`** **字段** — 持久化每角色的自定义参数

```json
// config_json 示例
{
  "rates": {
    "connection_toward_target": 0.1,
    "pride_decay": 0.02,
    ...
  },
  "thresholds": {
    "observation": 0.20,
    "consider_contact": 0.35,
    ...
  },
  "activities": {
    "reading": 0.7,
    "gaming": 0.6
  }
}
```

1. **合并逻辑：** `jiwen_core` 初始化时以 `DEFAULT_RATES/THRESHOLDS` 为底，然后 merge `config_json`。用户的覆盖默认，未覆盖的用默认。

***

### P1：SettingsView 重复定义 provider 元数据

**文件：** `SettingsView.vue:239-246`

```typescript
const _PROVIDER_DEFAULTS = {
  deepseek: { base_url: '...', default_model: 'deepseek-chat' },
  qwen: { base_url: '...', default_model: 'qwen-max' },
  ...
}
```

**问题：** 后端 `llm_settings_store.py` 第 62-94 行已经有 `PROVIDER_DEFAULTS`。如果后端新增 provider（如 `agnes`），前端不更新就看不到。

**决策：** 新增 `GET /api/llm/providers/meta` 端点，返回 `PROVIDER_DEFAULTS + PROVIDER_META`。前端 `SettingsView` 初始化时 fetch 这个端点，不自己维护列表。

***

### P1：主动消息 Fallback 模板硬编码

**文件：** `backend/modules/proactive.py:84-109`

6 个中文 fallback 模板（`"(嘴硬地)人呢？"` / `"不要想见我，我才没在想你……"`）全部硬编码。面向中文用户没问题，但如果角色设定是英文/日文语境，这 6 句话会破坏沉浸感。

**决策：**

1. **利用 JiwenState 的** **`config_json`** **存储** `fallback_templates: string[]`
2. **创建角色时**，根据 `world_setting` 的语境自动生成对应语言模板
3. **Fallback 链：** `config_json.fallback_templates` → `get_fallback_template()` 的默认中文模板 → `"（角色想要说点什么）"`

***

### P1：记忆衰减/摘要触发参数硬编码

| 文件                   | 参数                      |  数量 |
| :------------------- | :---------------------- | :-: |
| `memory_decay.py`    | 5 种主题衰减率 + 遗忘阈值         |  6  |
| `summary_trigger.py` | 最小/最大消息间隔 + 遗忘比例 + 时间间隔 |  4  |

**决策：**

1. **`memory_decay.py`：** 在 `Character` 模型中新增 `decay_config` JSON 字段。默认读 `THEME_DECAY_CONFIG`。最终方案与 jiwen 参数一致——默认 + 角色级覆盖。
2. **`summary_trigger.py`：** 在 `Character` 模型中新增 `summary_config` JSON 字段。同上。

两个字段合并为一个 `character_config` JSON 字段也可以，但 `config_json`（jiwen）/ `decay_config` / `summary_config` 语义分开更清晰。最终推荐 **三字段独立**，因为修改频率和影响范围不同。

***

### P2：Jiwen prompt 模板硬编码

**文件：** `jiwen_core.py`

| 函数                          |    行号   | 内容         |
| :-------------------------- | :-----: | :--------- |
| `_default_prompt_context()` | 585-615 | 自然语言状态描述模板 |
| `_default_style_guidance()` | 618-635 | 风格指引模板     |

**决策：** 移到 `Character.jiwen_config` JSON 字段的 `prompt_templates` 子键。允许角色定制自己的状态描述风格。

***

### P2：Session 复用窗口

**文件：** `jiwen_manager.py:655` → `timedelta(hours=24)`

**决策：** 改为从 `Character.session_config` JSON 字段读取，默认 24h。

***

## 三、整体架构决策

### 是否需要新增数据库字段？

| 新增字段                       | 类型   | 内容                                                                        |
| :------------------------- | :--- | :------------------------------------------------------------------------ |
| `Character.jiwen_config`   | JSON | rates / thresholds / activities / fallback\_templates / prompt\_templates |
| `Character.decay_config`   | JSON | theme\_decay / should\_forget\_threshold                                  |
| `Character.summary_config` | JSON | min\_messages / max\_messages / forgotten\_ratio / time\_gap\_days        |

**或者合并方案：** `Character.config`（一个 JSON 字段包含以上所有子键）。好处是迁移少，坏处是字段语义模糊。

**决策：合并为一个** **`Character.config`** **JSON 字段**，结构如下：

```json
{
  "jiwen": {
    "rates": { ... },
    "thresholds": { ... },
    "activities": { ... },
    "fallback_templates": ["...", "..."],
    "prompt_templates": { "context": "...", "style": "..." }
  },
  "decay": {
    "themes": { ... },
    "should_forget_threshold": 0.5
  },
  "summary": {
    "min_messages_between": 20,
    "max_messages_between": 100,
    "forgotten_ratio_trigger": 0.3,
    "time_gap_days": 7
  },
  "session": {
    "reuse_window_hours": 24
  }
}
```

只需要一次迁移，一个字段。

***

## 四、完整改动清单

|   优先级  | 改动                                     | 涉及文件                                                            |  类型 |
| :----: | :------------------------------------- | :-------------------------------------------------------------- | :-: |
| **P0** | CreateView 加 toast + 自动跳转 Chat         | `CreateView.vue` + 新建 `ToastProvider`                           |  前端 |
| **P0** | SettingsView active\_provider 从 API 获取 | `SettingsView.vue`                                              |  前端 |
| **P0** | Jiwen 参数 API + 前端面板                    | `jiwen_core.py` + `jiwen_router.py` + 新建 `JiwenConfigPanel.vue` | 前后端 |
| **P0** | `Character.config` JSON 字段 + 迁移        | `models.py` + `db_migration.py`                                 |  后端 |
| **P0** | `jiwen_core` 初始化时 merge config\_json   | `jiwen_core.py`                                                 |  后端 |
| **P1** | SettingsView provider 元数据从 API 获取      | `llm_router.py` + `SettingsView.vue`                            | 前后端 |
| **P1** | 主动消息 fallback 模板可配置                    | `proactive.py`                                                  |  后端 |
| **P1** | 记忆衰减/摘要触发参数可配置                         | `memory_decay.py` + `summary_trigger.py`                        |  后端 |
| **P2** | Jiwen prompt 模板可配置                     | `jiwen_core.py`                                                 |  后端 |
| **P2** | Session 复用窗口可配置                        | `jiwen_manager.py`                                              |  后端 |
|    —   | Settings 页 `window.alert` 改为现代 toast   | `SettingsView.vue`                                              |  前端 |

**总计：后端 8 处改动 + 前端 5 处 + 1 次 DB 迁移。**

***

## 五、执行顺序建议

```
Phase 1（1-2h）：P0 全部
  1. Character.config 迁移
  2. Jiwen 参数读写 API
  3. Jiwen 配置面板前端
  4. CreateView toast + 跳转
  5. SettingsView provider 元数据 API

Phase 2（1h）：P1 全部
  6. 主动消息 fallback 可配置
  7. 记忆衰减可配置
  8. 摘要触发可配置
  9. Settings 页 alert → toast

Phase 3（0.5h）：P2 全部
  10. prompt 模板可配置
  11. session 复用窗口可配置
```

需要我开始实现哪个 Phase，还是你有调整？
以下是逐文件、逐行的完整实现计划。

***

# 完整实现计划：创建成功反馈 + 设置/jiwen 硬编码

***

## Phase 1：P0（创建反馈 + jiwen 可配置 + 前后端一致）

***

### 1-1. 数据库迁移：`Character.config` 字段

**文件：** `backend/services/db_migration.py`

**新增函数** **`migrate_v008_character_config`（在 L285 之后插入）：**

```python
def migrate_v008_character_config(engine: Engine) -> dict:
    """
    迁移 v008：给 characters 表新增 config 列（角色级配置 JSON）

    config JSON 子结构：
      jiwen: { rates, thresholds, activities, fallback_templates, prompt_templates }
      decay: { themes, should_forget_threshold }
      summary: { min_messages_between, max_messages_between, 
                 forgotten_ratio_trigger, time_gap_days }
      session: { reuse_window_hours }
    """
    result = {"added_column": False}
    if not _sqlite_table_exists(engine, "characters"):
        return result
    cols = _sqlite_columns(engine, "characters")
    if "config" in cols:
        logger.debug("迁移 v008: characters.config 已存在，跳过")
        return result
    logger.info("迁移 v008: 添加 characters.config 列")
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE characters ADD COLUMN config TEXT"
        ))
    result["added_column"] = True
    logger.info("迁移 v008 完成: 新增列=%s", result["added_column"])
    return result
```

**修改** **`run_all_migrations()`（在 L321 后插入新条目）：**

```python
history.append({
    "version": "v008_character_config",
    **migrate_v008_character_config(engine),
})
```

**修改** **`Character`** **模型（`backend/models.py`** **L33 后新增一行）：**

```python
config = Column(Text, nullable=True)  # v008: 角色级配置 JSON（jiwen/decay/summary/session）
```

***

### 1-2. 后端：Jiwen 参数 API

**文件：** `backend/api/jiwen_router.py`

**新增端点** **`GET /api/jiwen/characters/{character_id}/params`（在 proactive stream 端点之后）：**

```python
@router.get("/api/jiwen/characters/{character_id}/params")
def get_character_params(character_id: int, db: Session = Depends(get_db)):
    """
    获取角色的 jiwen 可配置参数。
    返回默认值 + 角色级覆盖的合并结果。
    """
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 默认参数
    from backend.jiwen.jiwen_core import DEFAULT_RATES, DEFAULT_THRESHOLDS
    from backend.modules.memory_decay import THEME_DECAY_CONFIG
    from backend.modules.summary_trigger import (
        MIN_MESSAGES_BETWEEN, MAX_MESSAGES_BETWEEN,
        FORGOTTEN_RATIO_TRIGGER, TIME_GAP_DAYS,
    )

    # 角色级覆盖
    config = {}
    if character.config:
        try:
            config = json.loads(character.config)
        except json.JSONDecodeError:
            config = {}

    jiwen_config = config.get("jiwen", {})
    decay_config = config.get("decay", {})
    summary_config = config.get("summary", {})
    session_config = config.get("session", {})

    return {
        "character_id": character_id,
        "jiwen": {
            "rates": {**DEFAULT_RATES, **(jiwen_config.get("rates") or {})},
            "thresholds": {**DEFAULT_THRESHOLDS, **(jiwen_config.get("thresholds") or {})},
            "activities": jiwen_config.get("activities") or {
                "reading": 0.7, "search": 0.5, "browse": 0.4,
                "observe": 0.3, "none": 0.0,
            },
            "fallback_templates": jiwen_config.get("fallback_templates") or [],
            "prompt_templates": jiwen_config.get("prompt_templates") or {},
        },
        "decay": {
            "themes": decay_config.get("themes") or {
                k: {"base_decay_rate": v[0], "min_half_life_days": v[1],
                    "max_half_life_days": v[2]}
                for k, v in THEME_DECAY_CONFIG.items()
            },
            "should_forget_threshold": decay_config.get("should_forget_threshold", 0.5),
        },
        "summary": {
            "min_messages_between": summary_config.get("min_messages_between", MIN_MESSAGES_BETWEEN),
            "max_messages_between": summary_config.get("max_messages_between", MAX_MESSAGES_BETWEEN),
            "forgotten_ratio_trigger": summary_config.get("forgotten_ratio_trigger", FORGOTTEN_RATIO_TRIGGER),
            "time_gap_days": summary_config.get("time_gap_days", TIME_GAP_DAYS),
        },
        "session": {
            "reuse_window_hours": session_config.get("reuse_window_hours", 24),
        },
    }
```

**新增端点** **`PUT /api/jiwen/characters/{character_id}/params`：**

```python
@router.put("/api/jiwen/characters/{character_id}/params")
def update_character_params(
    character_id: int,
    params: Dict[str, Any],
    db: Session = Depends(get_db),
):
    """
    更新角色的可配置参数。
    请求体即 config JSON 内容（部分更新）。
    会自动刷新对应 jiwen 引擎实例。
    """
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 读取现有 config
    existing = {}
    if character.config:
        try:
            existing = json.loads(character.config)
        except json.JSONDecodeError:
            existing = {}

    # 深度 merge（不直接覆盖，保留未传的字段）
    for key in ("jiwen", "decay", "summary", "session"):
        if key in params and isinstance(params[key], dict):
            existing_key = existing.get(key, {})
            if isinstance(existing_key, dict):
                existing_key.update(params[key])
            else:
                existing_key = params[key]
            existing[key] = existing_key

    character.config = json.dumps(existing, ensure_ascii=False)
    db.commit()

    # 刷新 jiwen 引擎（如果已缓存）
    try:
        mgr = get_jiwen_manager()
        # 读取合并后的 rates/thresholds
        jiwen_cfg = existing.get("jiwen", {})
        rates = jiwen_cfg.get("rates")
        thresholds = jiwen_cfg.get("thresholds")
        if rates or thresholds:
            mgr.get_or_create_engine(character_id, rates=rates, thresholds=thresholds, refresh=True)
    except Exception as e:
        logger.warning("刷新 jiwen 引擎失败（参数已保存）: %s", e)

    return {"status": "ok", "character_id": character_id}
```

***

### 1-3. JiwenManager：从 `Character.config` 读取参数

**文件：** `backend/jiwen/jiwen_manager.py`

**修改** **`get_or_create_engine()`** **方法（L195-217 区域），在** **`create_jiwen()`** **调用前插入参数读取：**

```python
def get_or_create_engine(self, character_id, connection_rate_fn=None, 
                          rates=None, thresholds=None, refresh=False):
    with self._lock:
        if character_id in self._engines and not refresh:
            return self._engines[character_id]
        
        # v008: 如果调用方未传 rates/thresholds，从 Character.config 读取
        if rates is None or thresholds is None:
            try:
                with self._db() as db:
                    char = db.query(Character).filter(
                        Character.id == character_id
                    ).first()
                    if char and char.config:
                        cfg = json.loads(char.config)
                        jiwen_cfg = cfg.get("jiwen", {})
                        if rates is None:
                            rates = jiwen_cfg.get("rates")
                        if thresholds is None:
                            thresholds = jiwen_cfg.get("thresholds")
            except Exception as e:
                logger.warning("读取 character config 失败 (char_id=%s): %s", 
                              character_id, e)
        
        engine = create_jiwen(
            character_id=character_id,
            ...
            rates=rates,
            thresholds=thresholds,
            ...
        )
```

***

### 1-4. JiwenManager：Session 复用窗口可配置

**文件：** `backend/jiwen/jiwen_manager.py`

**修改** **`_find_or_create_session()`** **方法（L655 处** **`cutoff_time`** **计算）：**

```python
# 改前：
cutoff_time = datetime.utcnow() - timedelta(hours=24)

# 改后：
reuse_hours = 24  # 默认
try:
    if session_config and "reuse_window_hours" in session_config:
        reuse_hours = session_config["reuse_window_hours"]
    # 也可以从 Character.config 读取
    if character and character.config:
        cfg = json.loads(character.config)
        reuse_hours = cfg.get("session", {}).get("reuse_window_hours", 24)
except Exception:
    pass
cutoff_time = datetime.utcnow() - timedelta(hours=reuse_hours)
```

***

### 1-5. 主动消息：Fallback 模板从 Character.config 读取

**文件：** `backend/modules/proactive.py`

**修改** **`get_fallback_template()`** **函数（L84-109），增加参数并优先读 config：**

```python
def get_fallback_template(
    trigger_state: Dict[str, Any], 
    character_id: int = None,
    session_factory=None,
) -> str:
    # 优先从角色 config 读取自定义模板
    if character_id and session_factory:
        try:
            db = session_factory()
            char = db.query(Character).filter(Character.id == character_id).first()
            if char and char.config:
                cfg = json.loads(char.config)
                templates = cfg.get("jiwen", {}).get("fallback_templates", [])
                if templates:
                    import random
                    return random.choice(templates)
        except Exception:
            pass
    
    # Fallback：硬编码默认模板
    connection = trigger_state.get("connection", 0)
    pride = trigger_state.get("pride", 0)
    if connection >= 0.5:
        if pride >= 0.3:
            return "（嘴硬地）人呢？怎么不说话了？"
        else:
            return "在忙吗？想找你聊聊。"
    ...
```

同时修改 `generate_and_store_proactive_message()`（L188-244）的调用处，传入 `character_id`。

***

### 1-6. 记忆衰减/摘要触发：从 Character.config 读取

**文件：** `backend/modules/memory_decay.py`

**修改** **`THEME_DECAY_CONFIG`** **为可覆盖的函数（L32-40 区域）：**

新增函数：

```python
def get_theme_decay_config(db: Session = None, character_id: int = None) -> dict:
    """获取主题衰减配置（默认值 + 角色级覆盖）"""
    config = dict(THEME_DECAY_CONFIG)
    if db and character_id:
        try:
            char = db.query(Character).filter(Character.id == character_id).first()
            if char and char.config:
                cfg = json.loads(char.config)
                decay_cfg = cfg.get("decay", {}).get("themes", {})
                for theme, params in decay_cfg.items():
                    if theme in config and isinstance(params, dict):
                        config[theme] = (
                            params.get("base_decay_rate", config[theme][0]),
                            params.get("min_half_life_days", config[theme][1]),
                            params.get("max_half_life_days", config[theme][2]),
                        )
        except Exception:
            pass
    return config
```

**文件：** `backend/modules/summary_trigger.py`

修改 `should_summarize()` 函数，在读取阈值时增加角色级覆盖逻辑：

```python
def should_summarize(db, character_id, now=None):
    # 读取角色级配置
    min_msg = MIN_MESSAGES_BETWEEN
    max_msg = MAX_MESSAGES_BETWEEN
    forgotten_ratio = FORGOTTEN_RATIO_TRIGGER
    time_gap = TIME_GAP_DAYS
    try:
        char = db.query(Character).filter(Character.id == character_id).first()
        if char and char.config:
            cfg = json.loads(char.config).get("summary", {})
            min_msg = cfg.get("min_messages_between", min_msg)
            max_msg = cfg.get("max_messages_between", max_msg)
            forgotten_ratio = cfg.get("forgotten_ratio_trigger", forgotten_ratio)
            time_gap = cfg.get("time_gap_days", time_gap)
    except Exception:
        pass
    # ... 后续逻辑使用这些变量替代模块级常量
```

***

### 1-7. 前端：Toast 通知系统

**新建文件：** `web/src/composables/useToast.ts`

```typescript
import { ref } from 'vue'

interface Toast {
  id: number
  message: string
  type: 'success' | 'error' | 'info'
  duration: number  // ms, 0 = 手动关闭
}

const toasts = ref<Toast[]>([])
let nextId = 1

export function useToast() {
  function show(message: string, type: Toast['type'] = 'info', duration = 3000) {
    const id = nextId++
    toasts.value.push({ id, message, type, duration })
    if (duration > 0) {
      setTimeout(() => dismiss(id), duration)
    }
    return id
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  return { toasts, show, dismiss }
}
```

**新建文件：** `web/src/components/ToastContainer.vue`

```vue
<template>
  <div class="toast-container">
    <TransitionGroup name="toast">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="toast"
        :class="'toast-' + toast.type"
        @click="dismiss(toast.id)"
      >
        <span class="toast-icon">
          {{ toast.type === 'success' ? '✅' : toast.type === 'error' ? '❌' : 'ℹ️' }}
        </span>
        <span>{{ toast.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
defineProps<{ toasts: any[], dismiss: Function }>()
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.toast {
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  box-shadow: 0 2px 12px rgba(0,0,0,0.15);
  min-width: 220px;
  max-width: 360px;
}
.toast-success { background: #d1fae5; color: #065f46; }
.toast-error   { background: #fee2e2; color: #991b1b; }
.toast-info    { background: #e0e7ff; color: #3730a3; }
.toast-enter-active { transition: all 0.3s ease; }
.toast-leave-active { transition: all 0.2s ease; }
.toast-enter-from { opacity: 0; transform: translateX(40px); }
.toast-leave-to   { opacity: 0; transform: translateX(40px); }
</style>
```

**修改文件：** `web/src/App.vue`

在 `<script setup>`（L80 之后）添加：

```typescript
import { useToast } from '@/composables/useToast'
import ToastContainer from '@/components/ToastContainer.vue'
const { toasts, show: showToast, dismiss: dismissToast } = useToast()
// 暴露给子组件通过 inject 使用
provide('showToast', showToast)
```

在 `<template>` 中 `<main class="main">` 之上添加：

```html
<ToastContainer :toasts="toasts" :dismiss="dismissToast" />
```

***

### 1-8. 前端：CreateView 创建成功反馈

**文件：** `web/src/views/CreateView.vue`

**修改** **`submit()`** **方法（L199-220），在成功分支加入 toast 和自动跳转：**

```typescript
// 在 <script setup> 顶部添加
import { inject } from 'vue'
import { useRouter } from 'vue-router'
const showToast = inject<(msg: string, type?: string, duration?: number) => number>('showToast')!
const router = useRouter()

// 修改 submit() 内部：
async function submit() {
  // ... 不变 ...
  try {
    // ... 不变 ...
    result.value = created
    setActive(created.id)
    await refreshList()
    
    // ✅ 新增：toast 通知
    showToast(`角色「${created.name}」创建成功！`, 'success', 2500)
    
    // ✅ 新增：1.5s 后自动跳转到对话页
    setTimeout(() => {
      router.push('/chat')
    }, 1500)
    
    description.value = ''
    storyFile.value = null
  } catch (e) {
    // ... 不变 ...
  }
}
```

***

### 1-9. 前端：SettingsView 去掉硬编码的 `_PROVIDER_DEFAULTS`

**文件：** `web/src/views/SettingsView.vue`

**修改** **`loadAll()`** **L198-201，保存** **`defaults`：**

```typescript
// 新增响应式变量
const providerDefaults = ref<Record<string, { base_url: string; model: string }>>({})

// 修改 loadAll:
async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const [s, p] = await Promise.all([llmApi.get(), llmApi.providers()])
    settings.value = s
    providers.value = p.providers
    providerDefaults.value = p.defaults  // ← 新增：保存后端返回的 defaults
    syncForm(s)
  } catch (e) {
    error.value = e instanceof ApiError ? e.detail : (e as Error).message
  } finally {
    loading.value = false
  }
}
```

**删除 L239-252 整段（`_PROVIDER_DEFAULTS`** **+** **`defaultBaseUrl`** **+** **`defaultModel`），替换为：**

```typescript
function defaultBaseUrl(id: string): string {
  return providerDefaults.value[id]?.base_url ?? ''
}
function defaultModel(id: string): string {
  return providerDefaults.value[id]?.model ?? ''
}
```

**修改 L187 的** **`active_provider`** **默认值（删除靠不住的前端硬编码）：**

```typescript
const form = reactive({
  active_provider: '' as string,  // 改前: 'deepseek' → 改后: ''（由 loadAll 填充）
  active_config: { api_key: '', base_url: '', model: '' },
  ...
})
```

这样 `syncForm()` 在 `loadAll()` 成功时会把 `form.active_provider` 设置为后端实际值，避免前后端默认值不一致的 bug。

***

### 1-10. 前端：Jiwen 配置面板（Settings 页子 Tab）

**新建文件：** `web/src/components/JiwenConfigPanel.vue`

这个组件是按角色独立的 jiwen 参数编辑面板。放在 Settings 页面中作为 Tab 或折叠区域。

**核心结构：**

```vue
<template>
  <div class="jiwen-config">
    <!-- Tab 切换：情绪状态 / 记忆衰减 / 摘要触发 -->
    <nav class="config-tabs">
      <button v-for="tab in tabs" :key="tab.key" 
              :class="{ active: activeTab === tab.key }"
              @click="activeTab = tab.key">
        {{ tab.label }}
      </button>
    </nav>

    <!-- 情绪状态参数 -->
    <div v-if="activeTab === 'jiwen'" class="config-section">
      <h4>漂移率（Drift Rates）</h4>
      <!-- 15 个 slider -->
      <div v-for="(val, key) in params.jiwen.rates" :key="key" class="param-row">
        <label>{{ rateLabels[key] || key }}</label>
        <input type="range" v-model.number="params.jiwen.rates[key]" 
               :min="rateMins[key] || 0" :max="rateMaxs[key] || 0.1" 
               :step="rateSteps[key] || 0.001" />
        <span>{{ val.toFixed(4) }}</span>
      </div>

      <h4>触发阈值（Thresholds）</h4>
      <!-- 6 个 slider -->
      <div v-for="(val, key) in params.jiwen.thresholds" :key="key" class="param-row">
        <label>{{ thresholdLabels[key] || key }}</label>
        <input type="range" v-model.number="params.jiwen.thresholds[key]"
               :min="0" :max="1" :step="0.01" />
        <span>{{ val.toFixed(2) }}</span>
      </div>
    </div>

    <!-- 记忆衰减 -->
    <div v-if="activeTab === 'decay'" class="config-section">
      <!-- 5 主题衰减参数 -->
    </div>

    <!-- 摘要触发 -->
    <div v-if="activeTab === 'summary'" class="config-section">
      <!-- 4 个阈值 -->
    </div>

    <div class="config-actions">
      <button class="btn btn-primary" @click="save" :disabled="saving">保存</button>
      <button class="btn btn-ghost" @click="reset">恢复默认</button>
    </div>
  </div>
</template>
```

**路由集成（可选，最简单方案是 Settings 页内部 Tab）：** 把 JiwenConfigPanel 作为 SettingsView 的一个子组件，通过 `activeId` 判断当前选中的角色。

***

## Phase 2：P1

### 2-1. 前端：Settings 页 `window.alert` → toast

**文件：** `web/src/views/SettingsView.vue`

**修改 L278：**

```typescript
// 改前：
window.alert('已保存')

// 改后：
const showToast = inject<Function>('showToast')!
showToast('LLM 设置已保存', 'success')
```

同样的 toast 替换也适用于测试连接成功/失败提示。

***

### 2-2. API 新增 `consumeProactiveMessage` 函数（配套 Q5）

**文件：** `web/src/api/index.ts`

**在 llmSettings 对象之后新增：**

```typescript
export async function consumeProactiveMessage(
  characterId: number, 
  messageId: number
): Promise<{
  status: string
  session_id: number
  conversation_id: number
  character_id: number
} | null> {
  const res = await fetch(
    `${BASE}/jiwen/${characterId}/proactive-messages/${messageId}/consume`,
    { method: 'POST' }
  )
  if (!res.ok) return null
  return res.json()
}
```

***

### 2-3. 主动消息 Fallback 模板：前端 UI 入口

JiwenConfigPanel 的 jiwen Tab 中新增："主动消息模板"文本框列表（可添加/编辑/删除）。保存时写入 `params.jiwen.fallback_templates`。

***

## Phase 3：P2

### 3-1. Jiwen Prompt 模板可配置

**文件：** `backend/jiwen/jiwen_core.py`

**修改** **`get_prompt_context()`（L500-506）和** **`get_style_guidance()`（L508-514）：**

在 `config_json` 中查找 `prompt_templates` → 如果存在，用模板的 `context` 和 `style` 作为 `get_prompt_context_fn` / `get_style_guidance_fn` 传入 `create_jiwen()`。如果模板是纯文本，则用简单替换（`{connection}`, `{pride}` 等占位符）。

JiwenConfigPanel 的 jiwen Tab 中新增："情绪状态描述模板"和"风格指引模板"两个 textarea。

***

### 3-2. Session 复用窗口：前端 UI 入口

JiwenConfigPanel 的 "会话" Tab 中新增：一个 number input "主动消息 Session 复用窗口（小时）"。

***

## 文件改动总清单

|  序号 | 文件                                               |                               操作                              | Phase |
| :-: | :----------------------------------------------- | :-----------------------------------------------------------: | :---: |
|  1  | `backend/models.py`                              |                        修改：加 `config` 字段                       |   P0  |
|  2  | `backend/services/db_migration.py`               |                     新增 `migrate_v008` + 注册                    |   P0  |
|  3  | `backend/api/jiwen_router.py`                    |                    新增 GET/PUT `/params` 端点                    |   P0  |
|  4  | `backend/jiwen/jiwen_manager.py`                 |     修改 `get_or_create_engine` + `_find_or_create_session`     |   P0  |
|  5  | `backend/modules/proactive.py`                   |            修改 `get_fallback_template` 签名 + 读 config           |   P1  |
|  6  | `backend/modules/memory_decay.py`                |                 新增 `get_theme_decay_config` 函数                |   P1  |
|  7  | `backend/modules/summary_trigger.py`             |                 修改 `should_summarize` 读 config                |   P1  |
|  8  | `backend/jiwen/jiwen_core.py`                    |          修改 prompt\_context/style\_guidance 读 config          |   P2  |
|  9  | **新建** `web/src/composables/useToast.ts`         |                               新建                              |   P0  |
|  10 | **新建** `web/src/components/ToastContainer.vue`   |                               新建                              |   P0  |
|  11 | **新建** `web/src/components/JiwenConfigPanel.vue` |                               新建                              |   P0  |
|  12 | `web/src/App.vue`                                |                      修改：加 toast provider                      |   P0  |
|  13 | `web/src/views/CreateView.vue`                   |                        修改：toast + 自动跳转                        |   P0  |
|  14 | `web/src/views/SettingsView.vue`                 | 修改：删 `_PROVIDER_DEFAULTS` + alert→toast + 嵌入 JiwenConfigPanel | P0/P1 |
|  15 | `web/src/api/index.ts`                           |                新增 `consumeProactiveMessage` 函数                |   P1  |

**总计：8 个后端文件 + 7 个前端文件（含 3 个新建）+ 1 次 DB 迁移。**

\
无需额外读取代码，直接给出完整验收清单。

***

# 验收标准清单

***

## Phase 1 — P0 验收

### 1. DB 迁移

|  #  | 测试步骤                                      | 预期结果                                                     |
| :-: | :---------------------------------------- | :------------------------------------------------------- |
| 1.1 | 重启后端 → 查看启动日志                             | 日志出现 `迁移 v008 完成: 新增列=True` 或 `characters.config 已存在，跳过` |
| 1.2 | SQLite 执行 `PRAGMA table_info(characters)` | 列名中包含 `config`，类型 `TEXT`                                 |
| 1.3 | 重复重启后端 2 次                                | 第二次日志显示 `已存在，跳过`（幂等安全）                                   |

### 2. Jiwen 参数 API

|  #  | 测试步骤                                                                                     | 预期结果                                                                                   |
| :-: | :--------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------- |
| 2.1 | `GET /api/jiwen/characters/1/params`                                                     | 返回 200，包含 `jiwen.rates` (15个)、`jiwen.thresholds` (6个)、`decay`、`summary`、`session` 完整结构 |
| 2.2 | 对比返回的 `jiwen.rates` 与 `DEFAULT_RATES`                                                    | 数值完全一致（未配置过的角色返回默认值）                                                                   |
| 2.3 | `PUT /api/jiwen/characters/1/params` body=`{"jiwen":{"rates":{"prideRegression":0.05}}}` | 返回 `{"status":"ok"}`                                                                   |
| 2.4 | 再次 `GET /api/jiwen/characters/1/params`                                                  | `prideRegression` 变为 `0.05`，其他 14 个 rates 保持默认值                                        |
| 2.5 | `GET /api/jiwen/characters/999/params`（不存在的角色）                                           | 返回 404                                                                                 |
| 2.6 | 检查数据库 `characters.config` 字段                                                             | 值为 `{"jiwen":{"rates":{"prideRegression":0.05}}}` JSON                                 |
| 2.7 | `PUT` 更新 decay 参数 `{"decay":{"should_forget_threshold":0.3}}`                            | 返回 200，config JSON 中新增 `decay` 子键，jiwen 子键未被覆盖                                         |

### 3. Jiwen 参数生效（核心验收）

|  #  | 测试步骤                                                  | 预期结果                                              |
| :-: | :---------------------------------------------------- | :------------------------------------------------ |
| 3.1 | 修改角色 prideRegression 为 0.05（原默认 0.003）→ 等 5 分钟 → 查看日志 | 角色 pride 回归速度明显加快（每次 tick 下降 0.05×分钟 而非 0.003×分钟） |
| 3.2 | 修改 considerContact 阈值为 0.1（原 0.35）→ 等 tick            | connection 刚过 0.1 就触发 consider\_contact 动作        |
| 3.3 | 恢复默认值 → 等 tick                                        | 行为恢复默认                                            |

### 4. 创建成功反馈

|  #  | 测试步骤                              | 预期结果                                          |
| :-: | :-------------------------------- | :-------------------------------------------- |
| 4.1 | 打开页面 → 输入描述 → 点击「生成角色」→ 等待 LLM 返回 | 右上角弹出绿色 toast：`角色「{name}」创建成功！`               |
| 4.2 | Toast 行为                          | 2.5 秒后自动消失；点击 toast 可立即关闭                     |
| 4.3 | 自动跳转                              | 1.5 秒后自动从 `/create` 跳转到 `/chat` 页面，且刚创建的角色被选中 |
| 4.4 | 同时创建 2 个角色（连续点）                   | 每个成功都弹出 toast，toast 堆叠不覆盖                     |
| 4.5 | 创建失败（后端报错）                        | 弹出红色 toast 显示错误信息，不跳转，不显示预览卡片                 |

### 5. 前后端 Provider 一致（P0 Bug 修复）

|  #  | 测试步骤                                                      | 预期结果                                                              |
| :-: | :-------------------------------------------------------- | :---------------------------------------------------------------- |
| 5.1 | 首次打开 Settings 页（后端从未/刚初始化）                                | `active_provider` 显示与后端 `DEFAULT_ACTIVE` 一致（qwen），**不是** deepseek |
| 5.2 | 后端新增 provider（编辑 `PROVIDER_DEFAULTS` 加 provider X）→ 刷新设置页 | provider 列表出现 X，default base\_url/model 从 API 获取，无需改前端代码          |
| 5.3 | Settings 页保存 → 刷新页面 → 再次打开                                | 保存的 provider 保持选中，没有被默认值覆盖                                        |

### 6. Settings 页 `window.alert` → Toast

|  #  | 测试步骤            | 预期结果                                     |
| :-: | :-------------- | :--------------------------------------- |
| 6.1 | 修改 LLM 设置 → 点保存 | 弹出绿色 toast `LLM 设置已保存`，不是浏览器 alert 弹窗    |
| 6.2 | 测试连接成功          | toast 提示 `连接测试成功 ({model}, {latency}ms)` |
| 6.3 | 测试连接失败          | toast 提示 `连接测试失败: {原因}`                  |

***

## Phase 2 — P1 验收

### 7. 主动消息 Fallback 模板

|  #  | 测试步骤                                                                      | 预期结果                                              |
| :-: | :------------------------------------------------------------------------ | :------------------------------------------------ |
| 7.1 | 在 JiwenConfigPanel 中设置角色的 fallback\_templates 为 `["你还好吗？", "在忙什么？"]` → 保存 | `GET params` 返回的 `jiwen.fallback_templates` 包含这两条 |
| 7.2 | 触发主动消息（LLM 不可用场景）                                                         | 角色发出的消息是设置的两条之一（随机），不是硬编码的 `"（嘴硬地）人呢？"`           |
| 7.3 | 不配置 fallback\_templates（留空）                                               | 使用默认 6 条中文模板（向后兼容）                                |

### 8. 记忆衰减可配置

|  #  | 测试步骤                                               | 预期结果                                |
| :-: | :------------------------------------------------- | :---------------------------------- |
| 8.1 | 修改 identity 主题的 base\_decay\_rate 为 0.001（原 0.005） | 身份类记忆衰减速度变为原来的 1/5                  |
| 8.2 | 修改 should\_forget\_threshold 为 0.8                 | 记忆 strength 低于 0.8 才会被标记为遗忘（默认 0.5） |

### 9. 摘要触发可配置

|  #  | 测试步骤                                           | 预期结果             |
| :-: | :--------------------------------------------- | :--------------- |
| 9.1 | 修改 min\_messages\_between 为 5（原 20）→ 对话 6 条后检查 | 触发摘要（默认 20 条后触发） |
| 9.2 | 修改 forgotten\_ratio\_trigger 为 0.1（原 0.3）      | 遗忘 10% 就触发摘要     |

***

## Phase 3 — P2 验收

### 10. Jiwen Prompt 模板

|   #  | 测试步骤                                        | 预期结果                                                                |
| :--: | :------------------------------------------ | :------------------------------------------------------------------ |
| 10.1 | 修改 `prompt_templates.context` 为自定义中文模板 → 对话 | Director 的 `{current_state}` 或 Actor 的 `{scene_context}` 中出现自定义模板内容 |
| 10.2 | 修改 `prompt_templates.style` 为自定义风格指导 → 对话   | 角色语气按自定义模板变化                                                        |

### 11. Session 复用窗口

|   #  | 测试步骤                                              | 预期结果                                 |
| :--: | :------------------------------------------------ | :----------------------------------- |
| 11.1 | 修改 reuse\_window\_hours 为 1 → 创建主动消息 → 1 小时后创建第二条 | 1 小时后创建新 session，1 小时内复用同一 session   |
| 11.2 | 验证数据库 sessions 表                                  | 1 小时内只有 1 条新 session 记录，1 小时后出现第 2 条 |

***

## 通用验收

|  #  | 测试步骤                                         | 预期结果                  |
| :-: | :------------------------------------------- | :-------------------- |
| G.1 | 重启后端 3 次                                     | 每次正常启动，无 crash        |
| G.2 | 已有数据库中不存在的角色（characters 表为空）调用 GET params    | 返回 404                |
| G.3 | config JSON 被手动改坏（写非法 JSON 到 DB）→ GET params | 返回默认值（异常吞掉，不影响服务）     |
| G.4 | PUT params 传非法 JSON（非 dict）                  | 返回 422                |
| G.5 | 前端在旧浏览器（无 ES2020 支持）打开                       | 不报 JS 错误（无需 polyfill） |

***

## 快速自检脚本

验收时不需全部手动跑，这 5 个核心操作覆盖 >80% 风险点：

```
1. PUT params 改 prideRegression → GET params 确认 → 验证日志中 pride 变化速度
2. 创建角色 → 看到绿色 toast → 1.5s 自动跳到 /chat
3. 打开 Settings → active_provider 显示 qwen（不硬编码 deepseek）
4. 修改 fallback_templates → 触发主动消息 → 验证消息内容
5. 重启后端 → 日志无 error → config 列幂等迁移
```

全部通过 → 部署。
