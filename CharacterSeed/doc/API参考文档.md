# CharacterSeed API 参考文档

> **版本**: v0.1.0  
> **基础地址**: `http://localhost:8000`  
> **Swagger UI**: `http://localhost:8000/docs`  
> **生成日期**: 2026-06-22  

---

## 快速索引

| 模块 | 接口数量 | 说明 |
|------|---------|------|
| [角色管理](#1-角色管理) | 4 | CRUD 角色 |
| [对话交互](#2-对话交互) | 1 | 与角色对话 |
| [会话管理](#3-会话管理) | 5 | NextChat 风格多轮会话 |
| [成长系统](#4-成长系统) | 1 | 触发角色成长 |
| [事件推进](#5-事件推进) | 3 | Day4 事件推进 + 日迭代 |
| [角色数据查询](#6-角色数据查询) | 4 | 事件/记忆/对话/成长日志 |
| [LLM 设置](#7-llm-设置) | 4 | 配置管理 + 连通性测试 |
| [API 工具](#8-api-工具) | 3 | 模型列表/延迟测试/探针 |
| [系统](#9-系统) | 1 | 根路径健康检查 |

---

## 1. 角色管理

### 1.1 创建角色

```
POST /api/characters/create
```

**请求方式**: `multipart/form-data`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | `string` | 否 | 一句话描述（与 story_file 二选一） |
| `story_file` | `file (.txt)` | 否 | TXT 故事文件（与 description 二选一） |

> 若同时提供 `story_file` + `description`，`description` 将作为"额外角色期望"拼接到文件内容末尾。

**响应** `200` → `CharacterResponse`

```json
{
  "id": 1,
  "name": "艾琳",
  "description": "一个精灵弓箭手...",
  "world_setting": "中土魔法森林",
  "personality": "{\"勇敢\": 8, \"善良\": 9}",
  "current_state": "{\"位置\": \"森林\", \"心情\": \"平静\"}",
  "creation_raw": "...",
  "created_at": "2026-06-22T12:00:00Z",
  "updated_at": null,
  "day_number": 1,
  "speaking_style": "[\"温和而坚决\", \"喜欢用比喻\"]",
  "values": "[\"保护弱者\", \"尊重自然\"]",
  "habits": "[\"清晨练习射箭\", \"傍晚冥想\"]",
  "long_term_goal": "找到失落的精灵族圣物"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `int` | 角色 ID |
| `name` | `string` | 角色名 |
| `description` | `string?` | 用户原始输入（截断至 500 字） |
| `world_setting` | `string?` | LLM 生成的世界设定 |
| `personality` | `string?` | 人格属性（JSON 字符串，需 `JSON.parse()`） |
| `current_state` | `string?` | 当前状态（JSON 字符串） |
| `creation_raw` | `string?` | Creation LLM 原始 JSON 响应 |
| `created_at` | `datetime` | 创建时间 |
| `updated_at` | `datetime?` | 更新时间 |
| `day_number` | `int` | 当前天数（默认 1） |
| `speaking_style` | `string?` | 说话风格（JSON 数组字符串） |
| `values` | `string?` | 核心信念（JSON 数组字符串） |
| `habits` | `string?` | 日常习惯（JSON 数组字符串） |
| `long_term_goal` | `string?` | 长期目标 |

**错误码**:

| 状态码 | detail 模式 | 原因 |
|--------|------------|------|
| `400` | 必须提供 description 或 story_file | 两者均为空 |
| `500` | 角色创建失败: ... | LLM 调用或其他异常 |

---

### 1.2 获取角色列表

```
GET /api/characters
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `skip` | `int` | 否 | `0` | 跳过条数 |
| `limit` | `int` | 否 | `100` | 返回条数 |

**响应** `200` → `List[CharacterResponse]`

---

### 1.3 获取角色详情

```
GET /api/characters/{character_id}
```

**响应** `200` → `CharacterResponse`  
**错误** `404` → `{ "detail": "角色不存在" }`

---

### 1.4 删除角色（级联）

```
DELETE /api/characters/{character_id}
```

**级联顺序**: memories → conversations → growth_logs → characters

**响应** `200`

```json
{
  "detail": "角色「艾琳」及 12 条记忆、45 条对话、3 条成长记录已永久删除"
}
```

**错误** `404` → `{ "detail": "角色不存在" }`

---

## 2. 对话交互

### 2.1 与角色对话

```
POST /api/chat
```

**请求体** `JSON` → `ChatRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `character_id` | `int` | 是 | 角色 ID |
| `message` | `string` | 是 | 用户发送的消息 |
| `session_id` | `int?` | 否 | 会话 ID（`null` 时自动创建新会话） |

```json
{
  "character_id": 1,
  "message": "你好！今天天气真不错。",
  "session_id": null
}
```

**管线**: Director.analyze() → Actor.generate() → 持久化

**响应** `200` → `ChatResponse`

```json
{
  "id": 142,
  "character_id": 1,
  "user_input": "你好！今天天气真不错。",
  "npc_response": "是啊，阳光穿过树叶的感觉让人心情愉悦。",
  "emotion": "愉快",
  "action": "微笑着望向天空",
  "expression": "放松",
  "director_raw": "...",
  "actor_raw": "...",
  "timestamp": "2026-06-22T12:01:00Z",
  "session_id": 3,
  "session_title": "你好！今天天气真不错"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `int` | 对话记录 ID |
| `character_id` | `int` | 角色 ID |
| `user_input` | `string` | 用户输入 |
| `npc_response` | `string` | NPC 响应 |
| `emotion` | `string?` | Director 输出的情绪标签 |
| `action` | `string?` | 角色行动描述 |
| `expression` | `string?` | 角色表情 |
| `director_raw` | `string?` | Director LLM 原始响应 |
| `actor_raw` | `string?` | Actor LLM 原始响应 |
| `timestamp` | `datetime` | 对话时间 |
| `session_id` | `int?` | 所属会话（null 时自动生成） |
| `session_title` | `string?` | 会话标题（取首条消息前 30 字） |

**错误** `404` → 角色不存在 | `500` → 对话处理失败

---

## 3. 会话管理

> 参考 NextChat 设计：Session 是"多轮消息的容器"，1 个 Session 包含 N 条 Conversation。

### 3.1 列出会话

```
GET /api/sessions
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `character_id` | `int` | 是 | — | 角色 ID |
| `search` | `string?` | 否 | `null` | 按标题模糊搜索 |
| `limit` | `int` | 否 | `100` | 返回条数 |
| `offset` | `int` | 否 | `0` | 跳过条数 |

**响应** `200` → `List[ChatSessionInfo]`

```json
[
  {
    "id": 3,
    "character_id": 1,
    "title": "你好！今天天气真不错",
    "created_at": "2026-06-22T12:00:00",
    "updated_at": "2026-06-22T12:05:00",
    "message_count": 6
  }
]
```

**错误** `404` → 角色不存在

---

### 3.2 创建会话

```
POST /api/sessions
```

**请求体** `JSON` → `ChatSessionCreate`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `character_id` | `int` | 是 | 角色 ID |
| `title` | `string?` | 否 | 标题（null 时用 "新对话"） |

**响应** `200` → `ChatSessionInfo`

---

### 3.3 获取会话详情（含消息）

```
GET /api/sessions/{session_id}
```

**响应** `200` → `ChatSessionWithMessages`

```json
{
  "id": 3,
  "character_id": 1,
  "title": "你好！今天天气真不错",
  "created_at": "2026-06-22T12:00:00",
  "updated_at": "2026-06-22T12:05:00",
  "message_count": 2,
  "messages": [
    {
      "id": 142,
      "session_id": 3,
      "character_id": 1,
      "user_input": "你好！",
      "npc_response": "你好，冒险者。",
      "emotion": "友好",
      "action": null,
      "expression": null,
      "director_raw": null,
      "actor_raw": null,
      "timestamp": "2026-06-22T12:00:00"
    }
  ]
}
```

**错误** `404` → 会话不存在

---

### 3.4 重命名会话

```
PATCH /api/sessions/{session_id}
```

**请求体** `JSON` → `ChatSessionUpdate`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | `string` | 是 | 新标题 |

**响应** `200` → `ChatSessionInfo`  
**错误** `404` → 会话不存在

---

### 3.5 删除会话

```
DELETE /api/sessions/{session_id}
```

> 级联删除其下全部 Conversation（ON DELETE CASCADE）

**响应** `200`

```json
{ "deleted": true, "session_id": 3 }
```

**错误** `404` → 会话不存在

---

## 4. 成长系统

### 4.1 触发角色成长

```
POST /api/growth/trigger
```

**请求体** `JSON` → `GrowthTriggerRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `character_id` | `int` | 是 | 角色 ID |

**管线**: 获取角色 → 获取最近对话 → GrowthModule.run() → 输出 personality_delta / new_memories / event_summary → 持久化

**响应** `200` → `GrowthResponse`

```json
{
  "id": 5,
  "character_id": 1,
  "personality_delta": "{\"勇敢\": +1, \"社交\": +2}",
  "event_summary": "今天与冒险者进行了友好交流...",
  "new_memories": "[\"遇到了新朋友\", \"分享了精灵族的故事\"]",
  "growth_raw": "{...}",
  "schedule_json": "[{\"content\":\"...\",\"event_type\":\"...\"}]",
  "world_changes_json": "{\"change\":\"...\"}",
  "created_at": "2026-06-22T18:00:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `int` | GrowthLog ID |
| `personality_delta` | `string?` | 人格变化（JSON 对象字符串） |
| `event_summary` | `string?` | 事件摘要 |
| `new_memories` | `string?` | 新增记忆（JSON 数组字符串） |
| `growth_raw` | `string?` | Growth LLM 原始响应 |
| `schedule_json` | `string?` | 次日事件实体列表（JSON 数组字符串） |
| `world_changes_json` | `string?` | 世界变化（JSON 对象字符串） |

**错误** `404` → 角色不存在 | `500` → 成长处理失败

---

## 5. 事件推进

> Day4 引入的事件推进系统。Growth 迭代产出次日事件列表 → 用户逐个推进 → 完成后触发 Growth 迭代下一天。

### 5.1 推进单个事件

```
POST /api/event/advance
```

**请求体** `JSON` → `AdvanceRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `character_id` | `int` | 是 | 角色 ID |

**操作顺序**:
1. 打包未处理的对话 session → `player_dialogue` 事件（如有）
2. 取 `order_index` 最小的 `pending` 事件
3. 根据 `event_type` 写入 `result_json`
4. 标记 `status` → `completed`

**响应** `200` → `EventResponse`

```json
{
  "id": 10,
  "character_id": 1,
  "day_number": 1,
  "order_index": 1,
  "event_type": "schedule_action",
  "content": "清晨在森林中练习射箭",
  "metadata_json": null,
  "result_json": "角色完成了日程安排：清晨在森林中练习射箭",
  "status": "completed",
  "session_id": null,
  "time_period": "morning",
  "created_at": "2026-06-22T08:00:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `int` | 事件 ID |
| `character_id` | `int` | 角色 ID |
| `day_number` | `int` | 所属天数 |
| `order_index` | `int` | 排序序号 |
| `event_type` | `string` | 事件类型（见下方枚举） |
| `content` | `string` | 事件描述文本 |
| `metadata_json` | `string?` | 附加数据（对话事件含完整聊天 JSON） |
| `result_json` | `string?` | 执行回执（completed 后写入） |
| `status` | `string` | 状态：`pending` / `active` / `completed` |
| `session_id` | `int?` | 关联会话（对话事件专用） |
| `time_period` | `string?` | 时段：`morning` / `afternoon` / `evening` / `night` |

**event_type 枚举**:

| 值 | 说明 |
|----|------|
| `schedule_action` | 日程行动（角色按计划行动） |
| `scene_event` | 场景事件（环境触发） |
| `character_initiative` | 角色主动行动 |
| `player_dialogue` | 玩家对话记录（自动打包） |

**错误**:

| 状态码 | detail 模式 | 原因 |
|--------|------------|------|
| `404` | 角色不存在 | character_id 无效 |
| `404` | 角色 X 在 Day N 的所有事件已推进完成。请调用 /api/time/iterate | 所有事件已完成 |
| `404` | 角色 X 在 Day N 暂无待推进事件。 | 当前天无 pending 事件 |
| `500` | 事件推进失败 | 数据库写入失败 |

---

### 5.2 迭代一天

```
POST /api/time/iterate
```

**请求体** `JSON` → `IterateRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `character_id` | `int` | 是 | 角色 ID |

**操作顺序**:
1. 收集角色当天所有 `completed` 事件
2. 调用 GrowthModule.run() 输出人格变化 / 新记忆 / 次日日程
3. 将 `schedule` 逐项写入 events 表（`day_number+1`, `status=pending`）
4. 角色 `day_number += 1`

**响应** `200` → `IterateResponse`

```json
{
  "growth_log_id": 5,
  "character_id": 1,
  "day_number": 2,
  "personality_delta": "{\"勇敢\": +1}",
  "event_summary": "度过了充实的一天...",
  "new_memories": "[\"记忆1\", \"记忆2\"]",
  "world_changes_json": "{\"weather\": \"雨\"}",
  "schedule_json": "[{\"content\":\"清晨冥想\",\"event_type\":\"schedule_action\",\"time_period\":\"morning\",\"order_index\":1}]",
  "events_created": 3,
  "growth_raw": "{...}",
  "created_at": "2026-06-22T18:00:00"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `growth_log_id` | `int` | 成长日志 ID |
| `day_number` | `int` | 迭代后的新天数 |
| `events_created` | `int` | 写入 events 表的次日事件数量 |
| `schedule_json` | `string?` | 次日事件 JSON 数组（前端可展示日程预览） |

**错误** `404` → 角色不存在 | `500` → 成长分析失败

---

### 5.3 一键推演（自动模式）

```
POST /api/time/auto
```

**请求体** `JSON` → `AdvanceRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `character_id` | `int` | 是 | 角色 ID |

> 语法糖：串联"循环推进全部 pending 事件 → 自动触发迭代"

**响应** `200` → `AutoResponse`

```json
{
  "character_id": 1,
  "completed_events": [
    { "id": 10, "event_type": "schedule_action", "status": "completed" },
    { "id": 11, "event_type": "scene_event", "status": "completed" }
  ],
  "iterate_result": {
    "growth_log_id": 5,
    "day_number": 2,
    "events_created": 3
  },
  "error": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `completed_events` | `EventResponse[]` | 本次推进完成的事件列表 |
| `iterate_result` | `IterateResponse?` | 迭代结果（失败为 null） |
| `error` | `string?` | 整体错误信息 |

**错误** `404` → 角色不存在

---

## 6. 角色数据查询

### 6.1 获取事件列表

```
GET /api/characters/{character_id}/events
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `day_number` | `int?` | 否 | `null` | 筛选指定天 |
| `status` | `string?` | 否 | `null` | 筛选状态：`pending` / `completed` |

> 无 `day_number` 时返回所有天的事件（按 `day_number`, `order_index` 排序）

**响应** `200` → `List[EventResponse]`  
**错误** `404` → 角色不存在

---

### 6.2 获取记忆列表

```
GET /api/characters/{character_id}/memories
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `memory_type` | `string?` | 否 | `null` | 筛选类型：`conversation` / `event` / `growth` |
| `skip` | `int` | 否 | `0` | 跳过条数 |
| `limit` | `int` | 否 | `100` | 返回条数 |

**响应** `200` → `List[MemoryResponse]`

```json
[
  {
    "id": 1,
    "character_id": 1,
    "content": "遇到了新朋友...",
    "importance": 7,
    "memory_type": "conversation",
    "created_at": "2026-06-22T12:00:00Z"
  }
]
```

---

### 6.3 获取对话历史（旧式）

```
GET /api/characters/{character_id}/conversations
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `skip` | `int` | 否 | `0` | 跳过条数 |
| `limit` | `int` | 否 | `100` | 返回条数 |

> 注意：这是旧式全局对话列表，不按 session 分组。新的按 session 查询请使用 `GET /api/sessions/{session_id}`。

---

### 6.4 获取成长记录

```
GET /api/characters/{character_id}/growth-logs
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `skip` | `int` | 否 | `0` | 跳过条数 |
| `limit` | `int` | 否 | `100` | 返回条数 |

**响应** `200` → `List[GrowthResponse]`

---

## 7. LLM 设置

### 7.1 获取 LLM 设置

```
GET /api/settings/llm
```

**响应** `200` → `LLMSettingsResponse`

```json
{
  "active_provider": "deepseek",
  "active_provider_name": "DeepSeek",
  "config": {
    "api_key": "sk-****...abc1",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat"
  },
  "default_temperature": 0.7,
  "default_max_tokens": 2048,
  "providers": {
    "deepseek": { "api_key": "sk-****...abc1", "base_url": "...", "model": "..." },
    "openai": { "api_key": "sk-****...xyz9", "base_url": "...", "model": "..." },
    "ollama": { "api_key": "****", "base_url": "http://localhost:11434/v1", "model": "..." }
  },
  "settings_file_path": ".../llm_settings.json"
}
```

> `api_key` 均已脱敏：保留首尾 4 字符，中间以 `****` 替代。

---

### 7.2 列出支持的 Provider

```
GET /api/settings/llm/providers
```

**响应** `200`

```json
{
  "providers": [
    { "id": "deepseek", "name": "DeepSeek", "needs_key": "true" },
    { "id": "ollama", "name": "Ollama (本地)", "needs_key": "false" }
  ],
  "defaults": {
    "deepseek": { "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat" }
  }
}
```

---

### 7.3 更新 LLM 设置

```
PUT /api/settings/llm
```

**请求体** `JSON` → `LLMUpdateRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `active_provider` | `string?` | 否 | 切换激活的 provider（如 `"deepseek"`） |
| `active_config` | `ProviderConfig?` | 否 | 修改当前激活 provider 的配置 |
| `default_temperature` | `float?` | 否 | 默认温度（0.0 - 2.0） |
| `default_max_tokens` | `int?` | 否 | 默认最大 Token 数 |

**ProviderConfig**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `api_key` | `string?` | 否 | API Key |
| `base_url` | `string?` | 否 | API 基础地址 |
| `model` | `string?` | 否 | 模型名 |

**响应** `200` → `LLMSettingsResponse`（更新后的完整设置）  
**错误** `400` → 未知 provider

---

### 7.4 测试 LLM 连接

```
POST /api/settings/llm/test
```

**请求体** `JSON` → `LLMTestRequest`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `provider_id` | `string?` | 否 | 当前激活 | 指定 provider |
| `api_key` | `string?` | 否 | 读取现有 | 临时覆盖密钥 |
| `base_url` | `string?` | 否 | 读取现有 | 临时覆盖地址 |
| `model` | `string?` | 否 | 读取现有 | 临时覆盖模型 |
| `test_prompt` | `string?` | 否 | `"你好，请用一句话自我介绍。"` | 测试用提示词 |

> 所有覆盖仅在本次测试生效，**不写盘**。适合"先测试、再保存"场景。

**响应** `200` → `LLMTestResponse`

```json
{
  "success": true,
  "message": "连接成功（342ms）",
  "provider_id": "deepseek",
  "model": "deepseek-chat",
  "response_text": "你好！我是 DeepSeek，很高兴为你服务。",
  "latency_ms": 342
}
```

**失败示例**

```json
{
  "success": false,
  "message": "连接失败: Connection timeout",
  "provider_id": "openai",
  "model": "gpt-4",
  "response_text": null,
  "latency_ms": 20000
}
```

---

## 8. API 工具

### 8.1 拉取模型列表

```
GET /api/test/models
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `provider_id` | `string?` | 否 | provider ID（不传用当前激活） |
| `base_url` | `string?` | 否 | 临时覆盖地址 |
| `api_key` | `string?` | 否 | 临时覆盖密钥 |

**响应** `200` → `ModelsListResponse`

```json
{
  "provider_id": "deepseek",
  "base_url": "https://api.deepseek.com/v1",
  "models": [
    { "id": "deepseek-chat", "owned_by": "deepseek", "object": "model" },
    { "id": "deepseek-reasoner", "owned_by": "deepseek", "object": "model" }
  ],
  "duration_ms": 187,
  "raw_count": 2
}
```

**错误** `400` → 参数错误 | `502` → 远端请求失败 | `500` → 拉取异常

---

### 8.2 流式延迟测试

```
POST /api/test/latency
```

**请求体** `JSON` → `LatencyTestRequest`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `provider_id` | `string?` | 否 | 当前激活 | provider ID |
| `api_key` | `string?` | 否 | — | 临时覆盖密钥 |
| `base_url` | `string?` | 否 | — | 临时覆盖地址 |
| `model` | `string?` | 否 | — | 临时覆盖模型 |
| `test_message` | `string?` | 否 | `"Hi"` | 测试消息 |
| `max_tokens` | `int?` | 否 | `16` | 最大生成 Token |

**响应** `200` → `LatencyTestResponse`

```json
{
  "provider_id": "deepseek",
  "model": "deepseek-chat",
  "status": 1,
  "ttft_ms": 120,
  "total_ms": 450,
  "content": "你好！有什么我可以帮你的吗？",
  "chunks": 5,
  "error": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `int` | `1`=成功，`0`=失败 |
| `ttft_ms` | `int?` | Time To First Token (ms) |
| `total_ms` | `int?` | 完整响应耗时 (ms) |
| `chunks` | `int` | 流式数据块数量 |

---

### 8.3 原始请求探针

```
POST /api/test/probe
```

**请求体** `JSON` → `ProbeRequest`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `provider_id` | `string?` | 否 | 当前激活 | provider ID |
| `api_key` | `string?` | 否 | — | 临时覆盖密钥 |
| `base_url` | `string?` | 否 | — | 临时覆盖地址 |
| `test_message` | `string?` | 否 | `"Hi"` | 测试消息 |
| `max_tokens` | `int?` | 否 | `16` | 最大生成 Token |

> 返回完整请求/响应结构，用于排查鉴权/路由/协议差异。密钥字段已脱敏。

**响应** `200` → `ProbeResponse`

```json
{
  "provider_id": "deepseek",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com/v1",
  "request": {
    "method": "POST",
    "url": "https://api.deepseek.com/v1/chat/completions",
    "headers": { "Authorization": "Bearer sk-****...abc1", "Content-Type": "application/json" },
    "body": { "model": "deepseek-chat", "messages": [{"role": "user", "content": "Hi"}] }
  },
  "response": {
    "status_code": 200,
    "headers": { "content-type": "application/json" },
    "body": { "choices": [{"message": {"content": "Hello!"}}] }
  },
  "error": null
}
```

---

## 9. 系统

### 9.1 根路径

```
GET /
```

**响应** `200`

```json
{
  "message": "CharacterSeed API is running!",
  "docs": "http://localhost:8000/docs",
  "version": "0.1.0"
}
```

---

## 附录 A: 全局约定

### A.1 日期时间格式

所有 `datetime` 字段以 **ISO 8601** 格式返回：`"2026-06-22T12:00:00"` 或 `"2026-06-22T12:00:00Z"`（含时区）。

### A.2 JSON 字符串字段

`personality`、`current_state`、`speaking_style`、`values`、`habits`、`personality_delta`、`new_memories`、`schedule_json`、`world_changes_json` 等字段以 **JSON 字符串** 形式返回。前端使用时需自行 `JSON.parse()`：

```javascript
const personality = JSON.parse(character.personality);
// { "勇敢": 8, "善良": 9, ... }

const schedule = JSON.parse(iterateResp.schedule_json);
// [{ "content": "清晨冥想", "event_type": "schedule_action", ... }]
```

### A.3 错误响应格式

所有非 2xx 响应的统一格式：

```json
{
  "detail": "错误描述信息"
}
```

### A.4 字符编码

服务端统一使用 UTF-8。前端发送请求时请确保 `Content-Type: application/json; charset=utf-8`（JSON 请求）或 `multipart/form-data`（如创建角色的文件上传）。

---

## 附录 B: 数据模型关系

```
Character (1) ──< ChatSession (N) ──< Conversation (N)
Character (1) ──< Memory (N)
Character (1) ──< GrowthLog (N)
Character (1) ──< Event (N)
ChatSession (1) ──< Event (N)    [event_type=player_dialogue]
```

---

## 附录 C: 推荐调用流程

### C.1 创建角色 → 对话 → 推进 → 迭代（完整闭环）

```text
1. POST /api/characters/create       → 创建角色（自动写入 Day 1 事件）
2. POST /api/chat                    → 与角色对话
3. POST /api/event/advance           → 推进一个 pending 事件
4. （重复步骤 2-3 若干次直至所有事件完成）
5. POST /api/time/iterate            → 迭代到下一天（Growth 生成次日事件）
6. GET  /api/characters/{id}/events  → 查看事件时间轴
```

### C.2 一键推演

```text
1. POST /api/time/auto               → 自动推进全部事件 + 迭代
```

### C.3 LLM 配置切换

```text
1. GET  /api/settings/llm/providers  → 查看支持的 provider
2. POST /api/settings/llm/test       → 先测试连接
3. PUT  /api/settings/llm            → 保存配置
```

### C.4 会话式对话

```text
1. POST /api/chat                    → 发起对话（session_id=null，自动创建会话）
2. POST /api/chat                    → 继续对话（传入返回的 session_id）
3. GET  /api/sessions/{session_id}   → 查看当前会话全部消息
4. GET  /api/sessions?character_id=1 → 查看角色所有会话列表
```
