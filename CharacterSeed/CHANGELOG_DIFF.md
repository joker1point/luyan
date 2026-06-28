# Day4 事件推进系统 · 实现汇总

## 总体架构

```
[用户点击"推进事件"]
  → POST /api/event/advance {character_id}
    → 检查未打包对话 → 打包为 player_dialogue Event(status=completed)
    → 取下一个 status=pending 且 order_index 最小的 Event
    → 写入 result_json, status → completed
    → 返回 EventResponse

[用户点击"迭代一天"]
  → POST /api/time/iterate {character_id}
    → 收集当日全部 completed 的 Event
    → GrowthModule.run() → 人格变化 + 新记忆 + 次日日程
    → schedule[] → events 表(status=pending, day_number+1)
    → day_number += 1
    → 返回 IterateResponse

[用户点击"自动模式"]
  → POST /api/time/auto {character_id}
    → 循环 advance → advance → ... → iterate
    → 返回 AutoResponse(completed_events[], iterate_result)
```

## 修改文件清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/models.py` | MODIFY | Event新表 + Character新增day_number/speaking_style/values/habits/long_term_goal + GrowthLog新增schedule_json/world_changes_json |
| `backend/schemas.py` | MODIFY | EventResponse/AdvanceRequest/IterateRequest/IterateResponse/AutoResponse + CharacterResponse新5字段 + GrowthResponse新2字段 |
| `backend/services/db_migration.py` | MODIFY | 幂等迁移v002：建events表 + Character加5列 + GrowthLog加2列 |
| `backend/crud/event.py` | **NEW** | 8个事件CRUD函数：create/get_next_pending/get_events_by_day/complete/has_pending/get_day_number/count/delete |
| `backend/crud/__init__.py` | MODIFY | 注册event模块 |
| `backend/crud/character.py` | MODIFY | cascade_delete追加events清理 + create_character支持新4字段 |
| `backend/crud/growth.py` | MODIFY | create_growth_log支持schedule_json/world_changes_json参数 |
| `backend/modules/growth.py` | **REWRITE** | 事件驱动重写：run()读events表、输出schedule数组、validate_growth_schema_v2、long_term_goal更新 |
| `backend/services/llm_service.py` | MODIFY | 新增validate_growth_schema_v2 + validate_creation_schema新4字段校验 |
| `backend/prompts/growth.txt` | MODIFY | 输入改为事件列表(含content+result_json)+speaking_style/values/habits/long_term_goal；新增schedule/world_changes输出格式 |
| `backend/prompts/creation.txt` | MODIFY | 输出新增speaking_style(数组)/values(数组)/habits(数组)/long_term_goal(字符串) |
| `backend/modules/creation.py` | MODIFY | 注释更新 |
| `backend/main.py` | MODIFY | 新增POST /api/event/advance + POST /api/time/iterate + POST /api/time/auto + GET /api/characters/{id}/events；Creation端点持久化新4字段；对话打包辅助函数 |
| `frontend/api_client.py` | MODIFY | 新增advance_event/iterate_day/auto_advance/get_events 4个API函数 |
| `frontend/app.py` | MODIFY | 对话页新增"推进事件"/"迭代一天"/"自动模式"按钮组+完整结果展示；旧版growth触发保留向后兼容 |
| `tests/test_day4_event_system.py` | **NEW** | 24个核心单元测试，覆盖CRUD/schema校验/人格计算 |

## 关键技术决策

### 决策1：对话打包为 player_dialogue 事件
- **方案**：advance_event 前检查最新session是否有未打包对话，有则打包为一个Event(status=completed)
- **理由**：对话不能逐条成Event（碎片化），也不能替代Conversation表（session隔离）。打包让Growth以"一段对话"为粒度观测

### 决策2：事件状态机 pending → completed
- **方案**：Growth产出→pending，对话打包→completed，advance_event推进→completed
- **理由**：不设active状态简化状态管理；对话打包直接标记completed（对话已发生无需"进行中"）

### 决策3：Schedule 保底机制
- **方案**：validate_growth_schema_v2 中 schedule 为空时自动生成1条"新的一天开始了"
- **理由**：即使 LLM 输出异常，用户第二天也能看到至少1个待办事件，避免"空天"的困惑

### 决策4：双接口模式
- **控制台模式**：advance(手动推进) + iterate(手动迭代) — 用户完全控制节奏
- **自动模式**：auto(一键推演) — 语法糖，串联推进+迭代

## 核心代码注释原则

所有核心函数均包含 detailed docstring，注明：
1. **设计动机**：为什么需要这个函数/功能
2. **设计考量**：为什么选择这种实现方式（替代方案的取舍）
3. **边界条件**：空输入、异常、降级行为
4. **调用约定**：调用方需要知道什么

## 单元测试覆盖

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|----------|
| TestEventCRUD | 7 | create/get_next_pending/complete/has_pending/delete/空结果 |
| TestValidateGrowthSchemaV2 | 7 | 合法schedule/空schedule/非法event_type/非法time_period/空content/非字符串world_changes/非dict输入 |
| TestValidateCreationSchemaNewFields | 3 | 合法新字段/缺失fallback/空数组fallback |
| TestGrowthModuleHelper | 4 | 基本人格计算/上界钳位/下界钳位/事件格式化/空列表格式化 |
| TestEventFlowLogic | 4 | count统计/排序查询/天数推断/批量插入 |
