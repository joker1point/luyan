# Step 13-15 实现总结

> 基于 **v1.6 开发执行路线**，完整实施短期目标系统与测试验证。

---

## 一、变更概览

| 步骤 | 文件 | 操作 | 内容 |
|------|------|------|------|
| **Step 13** | `backend/prompts/creation.txt` | MODIFY | 新增第 14 字段 `short_term_goals` 输出格式要求 |
| | `backend/services/llm_service.py` | MODIFY | `validate_creation_schema()` 新增 `short_term_goals` 校验（6 条规则 + 保底） |
| | `backend/main.py` | MODIFY | `create_character` 端点持久化 `short_term_goals` |
| | `backend/crud/character.py` | MODIFY | `create_character` 函数签名新增 `short_term_goals` 参数 |
| **Step 14** | `backend/prompts/growth.txt` | MODIFY | 新增 `{short_term_goals_active}` 占位符 + `goal_updates`/`new_goals` 输出格式 |
| | `backend/modules/growth.py` | MODIFY | `run()` 注入活跃短期目标到 prompt；处理 Growth 产出的 `goal_updates` + `new_goals` 并持久化 |
| **Step 15** | `tests/test_steps_13_to_15.py` | **NEW** | 8 个综合测试用例，覆盖 Step 13/14 功能 + v1.6 全量 API 端点 |

---

## 二、Step 13 — Creation Prompt 新增 short_term_goals

### 做什么

在角色创建（Creation）阶段，让 LLM 基于 `long_term_goal` 产出 2-3 条**短期可执行目标**，作为长期目标与日程事件之间的桥梁。

### 底层逻辑

```
长期目标（抽象，"成为天下第一剑客"）
     ↓ 分解
短期目标（桥接，"找到剑术大师→挑战冠军→获得名剑"）
     ↓ 驱动
日程事件（具体，"Day 3 上午：去酒馆打听大师消息"）
```

**数据格式**（JSON 数组，存储在 `Character.short_term_goals` 列）：
```json
[
  {"goal": "找到一位剑术大师指点", "progress": 0.0, "created_day": 1, "source": "creation"},
  {"goal": "掌握基础剑术三式", "progress": 0.0, "created_day": 1, "source": "creation"}
]
```

**校验规则（6 条）**：
1. `goal` 必填非空字符串
2. `progress` 值域 [0.0, 1.0]，非法值钳位
3. `created_day` 至少为 1
4. `source` 白名单：`creation` / `growth` / `character`
5. 空 `goal` 目标跳过
6. 空数组/缺失 → 保底 1 条（基于 `long_term_goal` 生成）

---

## 三、Step 14 — Growth Prompt + Growth Module 扩展

### 做什么

在 Growth 迭代（每日结束）中，将角色的**未完成短期目标**注入到 LLM prompt 中，让 Growth LLM：
1. 评估每条目标的进展（`goal_updates`）
2. 生成新目标替换已完成的目标（`new_goals`）
3. 生成的 `schedule` 中至少 1 条事件与活跃目标对齐

### 底层逻辑

```
Growth 输入端新增：
  short_term_goals_active = [活跃目标列表]

Growth 输出端新增：
  goal_updates = [{index: 0, new_progress: 0.35, reason: "..."}]
  new_goals   = [{goal: "...", reason: "..."}]

应用层处理：
  → 根据 goal_updates[i].index 匹配活跃目标，更新 progress
  → 将 new_goals 去重后追加到目标列表
  → 持久化到 character.short_term_goals
```

**关键设计决策**：
- `goal_updates` 中的 `index` 对应 `short_term_goals_active` 数组的索引（0-based）
- Growth 是"上帝视角"迭代者：每天结束时做全量分析和目标评估
- 角色（通过 `modify_plan` 能力）是"第一人称"微调者：每个事件执行时做局部调整（Step 11 预留接口）
- 由于 `update_character()` 使用 `**kwargs`，`short_term_goals` 字段天然支持无需额外改造

---

## 四、测试覆盖详情（8 个用例）

| # | 测试用例 | 覆盖范围 | 验证点 |
|---|---------|---------|--------|
| 1 | `test_validate_creation_schema_with_short_term_goals` | Step 13 Schema 层 | 6 条校验规则：合法保留/缺失保底/空数组保底/progress 钳位/source 标准化/空 goal 跳过 |
| 2 | `test_create_character_persists_short_term_goals` | Step 13 持久化层 | 写入数据库→重新读取一致性/CharacterResponse 序列化/无目标角色兼容 |
| 3 | `test_growth_goal_updates_and_new_goals` | Step 14 Growth 集成 | 目标 progress 更新/新目标生成/去重/持久化验证/活跃目标过滤/无目标角色兼容 |
| 4 | `test_world_api_all_endpoints` | v1.6 World API | create_world / get_all_worlds / get_world / update_world / WorldResponse Schema |
| 5 | `test_scene_api_all_endpoints` | v1.6 Scene API | create_scene / get_scenes_by_world(layer) / get_scene_path / get_adjacent_scenes / update_scene(initial_description 不变) |
| 6 | `test_scene_change_api_all_endpoints` | v1.6 SceneChange API | create_scene_change / get_recent_changes(desc) / get_scene_changes_by_character / get_scene_changes_by_world / Schema |
| 7 | `test_character_world_integration_api` | v1.6 集成 API | get_world_by_character / get_scenes_by_character / CharacterResponse(world_id+scene_id) / 未关联世界优雅降级 |
| 8 | `test_full_lifecycle_with_short_term_goals` | E2E 全闭环 | 创建→World+Scene+Goals→Day1推进→Growth迭代→目标更新→Day2事件→SceneChange→全部Schema序列化 |

**运行结果**：`8 passed in 1.94s`

---

## 五、预定能跑通的验证点

### A. 短期目标系统

| # | 验证点 | 位置 | 预期行为 |
|---|--------|------|---------|
| A1 | Creation 产出初始目标 | `validate_creation_schema` | 缺少 `short_term_goals` 时自动生成 1 条保底，"创建后角色即有目标" |
| A2 | Progress 边界钳位 | Schema 校验 | `1.5 → 1.0`, `-0.5 → 0.0`, `"str" → 0.0` |
| A3 | Growth 注入活跃目标 | `growth.py` `run()` | 仅注入 `progress < 1.0` 的未完成目标，已完成的目标不注入 |
| A4 | Goal_updates 映射 | `growth.py` 处理层 | `goal_updates[].index` 通过 `short_term_goals_active` 索引映射到全量列表 |
| A5 | New_goals 去重 | `growth.py` 去重逻辑 | 与已有目标相同 `goal` 文本的不重复添加 |
| A6 | 全量更新持久化 | `character.short_term_goals` | 包含已完成目标 + 新目标 + 更新后的进度，JSON 数组格式 |
| A7 | 无目标角色兼容 | 空 `short_term_goals` | `None` 时 `_safe_load_json` 返回 `{}`，注入空数组 `[]` |

### B. v1.6 Phase 1 世界系统 API

| # | 验证点 | 端点 | 预期行为 |
|---|--------|------|---------|
| B1 | World CRUD | `create_world/get_world/update_world/get_all_worlds` | 创建→查询→更新→全量列表完整闭环 |
| B2 | Scene 两层树 | `create_scene(conceptual/actual)` | actual 的 `parent_scene_id` 必须指向 conceptual |
| B3 | Scene 路径 | `get_scene_path` | 从 actual 向上遍历 parent 到根，深度=层级数 |
| B4 | 兄弟场景 | `get_adjacent_scenes` | 同一 parent 下的其他场景，不含自身 |
| B5 | `initial_description` 不变 | `update_scene` | 更新 `description` 后，`initial_description` 保持创建时的原始值 |
| B6 | SceneChange 因果链 | `create_scene_change` | 每条记录保留 narrative 描述 + change_type + day_number |
| B7 | SceneChange 按天查询 | `get_scene_changes_by_world(day_number=X)` | 精确筛选指定天的变化 |
| B8 | Character-World 关联 | `get_world_by_character` | N:1 关系，`world_id` 为 None 时返回 None（优雅降级） |
| B9 | CharacterResponse 携带 v1.6 字段 | Schema 序列化 | `world_id` / `current_scene_id` / `short_term_goals` 三个新增字段全部就绪 |

### C. E2E 全闭环

| # | 验证点 | 阶段 | 数据流验证 |
|---|--------|------|-----------|
| C1 | 创建即拥有世界 | A | `char.world_id != None` 且有对应 World 行 |
| C2 | 创建即拥有目标 | A | `char.short_term_goals` 含 3 条初始目标 |
| C3 | Day 1 事件推进 | B | 4 个事件从 pending → completed |
| C4 | Growth 人格演化 | C | 人格 6 维 delta 正确计算（钳位 [0,100]） |
| C5 | Growth 目标更新 | C | 1 条完成（progress=1.0）+ 2 条新目标（source=growth） |
| C6 | SceneChange 记录 | C | 场景变化写入并关联 growth_log_id |
| C7 | Day 2 事件对齐 | C | schedule 正确生成 Day 2 的 pending 事件 |
| C8 | 全部 v1.6 Schema 序列化 | D | WorldResponse / SceneResponse / SceneChangeResponse / CharacterResponse 全部正确 |
