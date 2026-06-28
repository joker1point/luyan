# 2026-06-27 — jiwen 集成 + 记忆/遗忘系统设计

> 目标：把 `https://github.com/ClaraShafiq/jiwen` 的"积温引擎"集成进 CharacterSeed，并在此基础上完成记忆/遗忘系统的设计闭环。

---

## 0. 设计哲学

数据全本地、低延迟、零跨进程。**jiwen 是 Node.js 库，但 CharacterSeed 是 Python——所以直接算法移植**，不引入 HTTP sidecar。算法是纯数学漂移，无 Node 特有 API，可 1:1 移植。

---

## 1. 架构总览

```
                  ┌────────────────────────────────────────┐
                  │       FastAPI (Python)                 │
                  │                                        │
   User Input ──>│  InteractionPipeline                   │
                  │       │                                │
                  │       ├──> Director (LLM)              │
                  │       │       ↓                       │
                  │       ├──> Actor (LLM, stream)         │
                  │       │       ↓                       │
                  │       ├──> Conversation CRUD          │
                  │       │                                │
                  │       └──> Jiwen Engine ◀────┐        │
                  │              │              │        │
                  │              ↓              │        │
                  │       applyDelta() ─────────┘        │
                  │              ↓                       │
                  │       DB: jiwen_state (per char)     │
                  │                                        │
                  │  Background Tick (asyncio task)       │
                  │       │ every 5min                    │
                  │       ↓                               │
                  │  jiwen.tick() → triggers[]            │
                  │       │                               │
                  │       ├── observation → logs          │
                  │       ├── contact → proactive queue   │
                  │       └── find_activity → state only  │
                  │                                        │
                  │  Memory/Forgetting System             │
                  │       ├── L1: Conversation (永不删)  │
                  │       ├── L2: memory_summaries (新)   │
                  │       └── L3: Memory (增强 metadata) │
                  └────────────────────────────────────────┘
```

---

## 2. jiwen 移植方案

### 2.1 核心算法（5 轴漂移）

| 轴 | 范围 | 漂移规则 | 触发器 |
|----|------|----------|--------|
| **connection** | 0→1 | 基础率 × 加速因子 × 情绪因子 | ≥0.20 observation / ≥0.35 consider / ≥0.50 force |
| **pride** | -1→+1 | 0.003/min 回归 0；被冷落时防御性升 | ≥0.5 阻断 contact |
| **valence** | -1→+1 | 0.005/min 回归 setpoint；想强时减速 | ≤valenceActivity 触发 find_activity |
| **arousal** | -1→+1 | 0.005/min 回归；想强时升 | ≥arousalAgitation 触发 find_activity |
| **immersion** | 0→1 | 0.01/min 衰减 | `setActivity` 可部分缓解 |

### 2.2 Python API（1:1 移植）

```python
jiwen = create_jiwen(
    character_id=1,
    get_last_message=lambda: db.query(...),
    connection_rate_fn=lambda msg: 0.0007,  # default
    on_save=lambda state: db.set(...),
    on_load=lambda: db.get(...),
)

triggers = jiwen.tick(minutes=5)         # 推进状态，返回触发器
jiwen.apply_delta({pride: -0.1, ...})   # 聊天后调整
ctx = jiwen.get_prompt_context()         # 状态 → 自然语言
style = jiwen.get_style_guidance()       # 状态 → 风格指引
```

### 2.3 与 CharacterSeed 的衔接点

| jiwen 触发 | CharacterSeed 路径 |
|-----------|-------------------|
| observation | 仅写 `jiwen_triggers` 日志表（不打扰用户） |
| contact | 入 `proactive_messages` 队列 → 前端 SSE 推送 |
| find_activity | 更新 immersion / activity_type（无外显） |

---

## 3. 5 个默认值的最终决策

| # | 问题 | 决策 |
|---|------|------|
| 1 | ChromaDB metadata | **暂不接 ChromaDB**（CharacterSeed 当前没用），Memory 表增强 metadata 字段：`strength/importance/recall_count/last_recalled_at/theme/forgotten/decay_rate` |
| 2 | L2 摘要存储 | **新建 `memory_summaries` 表**（SQL 一致性） |
| 3 | 提取 LLM | **复用 Director LLM** + 新建 `extractor.txt` 提示词（temperature=0.3, JSON） |
| 4 | 当前指标 | **0/greenfield** |
| 5 | jiwen 输出格式 | **结构化 JSON** `{connection, pride, valence, arousal, immersion, last_chat_message_id, user_status, activity_type, activity_label, last_tick_at}` |

---

## 4. 记忆/遗忘系统设计

### 4.1 三层架构（落地版）

| 层 | 表 | 行为 | 遗忘 |
|----|----|------|------|
| **L1 底片** | `conversations`（已有） | 永不删 | ❌ 不忘 |
| **L2 滚动摘要** | `memory_summaries`（新） | **自适应触发**（不再固定 50 条） | 旧摘要标 `superseded_by`，但保留 |
| **L3 记忆碎片** | `memories`（增强） | 提取时打 importance / strength / theme | soft-deprecate（`forgotten=true`），从检索池过滤掉 |

### 4.2 重要性评分（importance 0-1）

```python
def score_importance(content, director_data, actor_data):
    """
    0.4 × emotion_强度 + 0.3 × user_disclosure + 0.2 × 行动相关 + 0.1 × 引用已知主题
    """
    return round(min(1.0, max(0.0, score)), 3)
```

### 4.3 衰减函数（Ebbinghaus + importance 修正）

```python
def decay(strength, importance, age_days):
    """
    默认半衰期 7 天；importance 越高，半衰期越长（最长 90 天）
    """
    half_life = 7 * (1 + importance * 12)  # 7 ~ 91 天
    return strength * (0.5 ** (age_days / half_life))
```

### 4.4 自适应摘要触发器（替代固定 50 条）

```python
should_summarize(character_id) -> bool:
    if msg_count_since_last_summary < 20:  # 下限：避免过频
        return False
    if msg_count_since_last_summary > 100:  # 上限：避免过久
        return True
    forgotten_ratio = get_forgotten_ratio(character_id)  # forgotten / total
    return forgotten_ratio > 0.3
```

### 4.5 RRF + 强度 boost（如果未来接 ChromaDB）

```python
final_score = rrf_score + boost_factor
where boost_factor = log(1 + recall_count) * exp(-λ * age_days) * importance
```

---

## 5. 数据库 schema（新增）

### 5.1 `jiwen_states` 表

```python
class JiwenState:
    character_id     # PK
    connection       # 0-1
    pride            # -1 to 1
    valence          # -1 to 1
    arousal          # -1 to 1
    immersion        # 0-1
    last_chat_msg_id # 用于"对方最后说了什么"
    user_status      # active/busy/away/sleeping
    activity_type    # reading/search/browse/observe/none
    activity_label   # 自由文本
    last_tick_at     # 时间戳
    updated_at
```

### 5.2 `memory_summaries` 表（新）

```python
class MemorySummary:
    id
    character_id
    summary_text
    msg_start_id
    msg_end_id
    importance_score  # 0-1
    superseded_by     # 指向新摘要的 id（链式）
    created_at
```

### 5.3 `memories` 表增强

新增字段：
- `theme` (identity/music/taste/moment/todo) — 来自 SonettoHere 5 分区
- `strength` (0-1, 默认 0.5)
- `importance` (0-1, 计算得出)
- `recall_count` (int, 默认 0)
- `last_recalled_at` (timestamp)
- `forgotten` (bool, 默认 false)
- `decay_rate` (float, 按主题差异化)

---

## 6. 文件结构（新增）

```
backend/
  jiwen/
    __init__.py
    jiwen_core.py          # 纯算法移植 (~500 行)
    jiwen_manager.py       # 生命周期 + DB 持久化封装
    tone_grid.py           # 9 簇 × 5 档语调网格（可选）
  modules/
    memory_decay.py        # 衰减引擎
    memory_extractor.py    # 提取（事实/偏好/情绪碎片）
    summary_trigger.py     # 自适应摘要触发
  prompts/
    extractor.txt          # 记忆提取提示词
  api/
    jiwen_router.py        # REST API
  models.py                # 增加 JiwenState / MemorySummary 模型
  database.py              # 不变（自动 create_all）
```

---

## 7. 实施顺序

1. **jiwen_core.py** — 纯算法，无依赖
2. **DB models** — JiwenState + MemorySummary + 增强 Memory
3. **jiwen_manager.py** — 状态加载/保存
4. **InteractionPipeline 集成** — applyDelta + getPromptContext
5. **memory_extractor.py** — 复用 Director LLM
6. **memory_decay.py** — 衰减引擎
7. **summary_trigger.py** — 自适应触发
8. **background tick** — asyncio task
9. **jiwen_router.py** — REST API
10. **tests/** — pytest

---

## 8. 验收标准

- [x] jiwen_core.py 单测 24+ 项，逻辑与原 JS 版对齐
- [x] tick(minutes) 返回的 triggers 与原 JS 版等价
- [x] getPromptContext 输出自然语言，可注入 Director system prompt
- [x] applyDelta 后状态正确漂移
- [x] DB 持久化无丢失（重启后状态恢复）
- [x] 后台 tick 正常调度（不阻塞主服务）
- [x] InteractionPipeline.run 后自动 applyDelta
- [x] 记忆提取 5 分区（identity/music/taste/moment/todo）正确分类
- [x] 衰减函数 7 天半衰期符合 Ebbinghaus
- [x] 自适应摘要触发器：forgotten_ratio>0.3 OR msg_count>100
- [x] REST API: GET /api/jiwen/{cid}/state, POST /api/jiwen/{cid}/delta, GET /api/jiwen/{cid}/triggers

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| jiwen 算法移植偏差 | 写"算法等价性"测试，与原 JS 版同输入对比输出 |
| 后台 tick 抢资源 | asyncio.create_task，单独 try/except，单次 tick O(活跃角色数) |
| 状态漂移"夜长梦多" | 注入最大漂移时长上限（24h）防止状态爆掉 |
| 用户首次使用无 last_message | connection 增长曲线从 0 起步，0.0007/min 缓慢累积 |
| 跨时区漂移 | last_tick_at 用 UTC 存，display 用本地时区 |

---

## 10. 实施交付清单（2026-06-27 完成）

### 10.1 新增文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `backend/jiwen/jiwen_core.py` | ~520 | 5 轴漂移算法 + 触发器（Node.js → Python 1:1 移植） |
| `backend/jiwen/jiwen_manager.py` | ~360 | 单例 + DB 持久化 + session_factory 注入 |
| `backend/jiwen/jiwen_scheduler.py` | ~180 | 后台 tick 调度（asyncio） |
| `backend/modules/memory_decay.py` | ~210 | Ebbinghaus 衰减 + soft-deprecate |
| `backend/modules/memory_extractor.py` | ~240 | LLM 提取 5 分区记忆（identity/music/taste/moment/todo） |
| `backend/modules/summary_trigger.py` | ~290 | 自适应摘要触发 + chain supersede |
| `backend/api/jiwen_router.py` | ~330 | 17 个 REST 端点 |
| `tests/test_jiwen_core.py` | ~430 | 24+ 单测（5 轴 / 触发器 / drift） |
| `tests/test_memory_decay.py` | ~180 | 衰减算法单测 |
| `tests/test_jiwen_integration.py` | ~410 | 集成 + REST API + Pipeline 注入 |

### 10.2 修改文件

- `backend/models.py` — 新增 `JiwenState` / `MemorySummary` / `JiwenTrigger` 表，`Memory` 增强 metadata
- `backend/modules/interaction.py` — InteractionPipeline 注入 jiwen：`current_state._jiwen` 子字段 + Actor style 追加 `get_style_guidance()`
- `backend/modules/post_chat.py` — 聊天后钩子（applyDelta + extract + decay + summary）
- `backend/main.py` — `app.include_router(jiwen_router.router)`
- `tests/conftest.py` — 三件套 session_factory 注入（jiwen_manager / post_chat / get_db）

### 10.3 测试结果

```
tests/test_jiwen_core.py        : 30 passed
tests/test_memory_decay.py      : 27 passed
tests/test_jiwen_integration.py : 30 passed
================================
合计                            : 87 passed in 2.14s
```

### 10.4 5 个默认值的最终落地

| # | 问题 | 实际落地 |
|---|------|----------|
| 1 | ChromaDB metadata | **暂不接 ChromaDB**；Memory 表已加 `strength/importance/recall_count/last_recalled_at/theme/forgotten/decay_rate` |
| 2 | L2 摘要存储 | **新建 `memory_summaries` 表**（含 `superseded_by` 链式） |
| 3 | 提取 LLM | **复用 Director LLM**（不新建 `extractor.txt`）；`memory_extractor.py` 内部 schema-prompt |
| 4 | 当前指标 | **0/greenfield**（无 ChromaDB / 无 prometheus） |
| 5 | jiwen 输出格式 | **结构化 JSON**（jiwen_state 落库 + 7 个 REST 端点） |

### 10.5 API 端点（17 个）

```
GET    /api/jiwen/characters
GET    /api/jiwen/{cid}/state
POST   /api/jiwen/{cid}/state
POST   /api/jiwen/{cid}/delta
GET    /api/jiwen/{cid}/triggers
POST   /api/jiwen/{cid}/tick
GET    /api/jiwen/{cid}/prompt-context
GET    /api/jiwen/{cid}/style-guidance
POST   /api/jiwen/{cid}/activity
POST   /api/jiwen/{cid}/user-status
POST   /api/jiwen/scheduler/start
POST   /api/jiwen/scheduler/stop
GET    /api/jiwen/scheduler/status
POST   /api/jiwen/scheduler/tick-now
GET    /api/jiwen/{cid}/memory-stats
POST   /api/jiwen/{cid}/run-decay
POST   /api/jiwen/{cid}/check-summary
GET    /api/jiwen/{cid}/summaries
```

---

## 11. 后续 P1/P2（不在本次范围）

- 接 ChromaDB（metadata 字段已就绪，只差 vector index 写入）
- 跨设备 jiwen 状态同步（当前是 local-only）
- 自适应温度（jiwen 强烈情绪 → Director 降温，平静 → 升温）
- 摘要生成 LLM prompt 优化（当前 fallback 较 bland）
