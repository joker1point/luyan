/**
 * CharacterSeed Web — TypeScript 类型定义
 * 映射 API 参考文档的所有响应/请求体
 */

// ============================================================================
// 1. 角色管理
// ============================================================================

export interface Character {
  id: number
  name: string
  description: string | null
  world_setting: string | null
  /** JSON 字符串，结构如 {"勇敢": 8, "善良": 9} */
  personality: string | null
  /** JSON 字符串，结构如 {"位置": "森林", "心情": "平静"} */
  current_state: string | null
  creation_raw: string | null
  created_at: string
  updated_at: string | null
  day_number: number
  /** JSON 数组字符串 */
  speaking_style: string | null
  /** JSON 数组字符串 */
  values: string | null
  /** JSON 数组字符串 */
  habits: string | null
  long_term_goal: string | null
}

export interface CreateCharacterRequest {
  description?: string
  story_file?: File
}

/** 解析后的 personality 对象 */
export type PersonalityMap = Record<string, number>

/** 解析后的 current_state 对象 */
export interface CurrentState {
  location?: string
  activity?: string
  mood?: string
  [key: string]: string | undefined
}

// ============================================================================
// 2. 对话交互
// ============================================================================

export interface ChatRequest {
  character_id: number
  message: string
  session_id?: number | null
}

export interface ChatResponse {
  id: number
  character_id: number
  user_input: string
  npc_response: string
  emotion: string | null
  action: string | null
  expression: string | null
  director_raw: string | null
  actor_raw: string | null
  timestamp: string
  session_id: number | null
  session_title: string | null
}

// ============================================================================
// 3. 会话管理
// ============================================================================

export interface ChatSessionInfo {
  id: number
  character_id: number
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface ChatSessionCreate {
  character_id: number
  title?: string | null
}

export interface ChatSessionUpdate {
  title: string
}

export interface ChatSessionWithMessages extends ChatSessionInfo {
  messages: ChatResponse[]
}

// ============================================================================
// 4. 成长系统
// ============================================================================

export interface GrowthTriggerRequest {
  character_id: number
}

export interface GrowthResponse {
  id: number
  character_id: number
  personality_delta: string | null
  event_summary: string | null
  new_memories: string | null
  growth_raw: string | null
  schedule_json: string | null
  world_changes_json: string | null
  created_at: string
}

// ============================================================================
// 5. 事件推进
// ============================================================================

export type EventType = 'schedule_action' | 'scene_event' | 'character_initiative' | 'player_dialogue'
export type EventStatus = 'pending' | 'active' | 'completed'
export type TimePeriod = 'morning' | 'afternoon' | 'evening' | 'night'

export interface EventResponse {
  id: number
  character_id: number
  day_number: number
  order_index: number
  event_type: EventType
  content: string
  metadata_json: string | null
  result_json: string | null
  status: EventStatus
  session_id: number | null
  time_period: TimePeriod | null
  created_at: string
}

export interface AdvanceRequest {
  character_id: number
}

export interface IterateRequest {
  character_id: number
}

export interface IterateResponse {
  growth_log_id: number
  character_id: number
  day_number: number
  personality_delta: string | null
  event_summary: string | null
  new_memories: string | null
  world_changes_json: string | null
  schedule_json: string | null
  events_created: number
  growth_raw: string | null
  created_at: string
}

export interface AutoResponse {
  character_id: number
  completed_events: EventResponse[]
  iterate_result: IterateResponse | null
  error: string | null
}

// ============================================================================
// 6. 角色数据查询
// ============================================================================

export type MemoryType = 'conversation' | 'event' | 'growth'

export interface MemoryResponse {
  id: number
  character_id: number
  content: string
  importance: number
  memory_type: MemoryType
  created_at: string
}

// ============================================================================
// 7. LLM 设置
// ============================================================================

export interface ProviderConfig {
  api_key: string
  base_url: string
  model: string
}

export interface LLMProviderInfo {
  id: string
  name: string
  needs_key: 'true' | 'false'
}

export interface LLMSettingsResponse {
  active_provider: string
  active_provider_name: string
  config: ProviderConfig
  default_temperature: number
  default_max_tokens: number
  providers: Record<string, ProviderConfig>
  settings_file_path: string
}

export interface LLMUpdateRequest {
  active_provider?: string
  active_config?: ProviderConfig
  default_temperature?: number
  default_max_tokens?: number
}

export interface LLMTestRequest {
  provider_id?: string
  api_key?: string
  base_url?: string
  model?: string
  test_prompt?: string
}

export interface LLMTestResponse {
  success: boolean
  message: string
  provider_id: string
  model: string
  response_text: string | null
  latency_ms: number
}

// ============================================================================
// 8. API 工具
// ============================================================================

export interface ModelItem {
  id: string
  owned_by: string
  object: string
}

export interface ModelsListResponse {
  provider_id: string
  base_url: string
  models: ModelItem[]
  duration_ms: number
  raw_count: number
}

export interface LatencyTestRequest {
  provider_id?: string
  api_key?: string
  base_url?: string
  model?: string
  test_message?: string
  max_tokens?: number
}

export interface LatencyTestResponse {
  provider_id: string
  model: string
  status: 0 | 1
  ttft_ms: number | null
  total_ms: number | null
  content: string | null
  chunks: number
  error: string | null
}

// ============================================================================
// 9. UI 状态类型
// ============================================================================

/** 聊天消息（含本地状态） */
export interface ChatMessageItem {
  id: number
  role: 'user' | 'assistant'
  content: string
  emotion?: string | null
  action?: string | null
  expression?: string | null
  director_raw?: string | null
  actor_raw?: string | null
  timestamp: string
  /** 前端生成，等待后端响应时使用 */
  pending?: boolean
  /** 后端 thinking 阶段上报（starting / directing / acting / cache_hit） */
  thinking_phase?: string | null
  /** thinking 阶段附带的中文提示 */
  thinking_message?: string | null
  error?: string
}

/** 创建角色的本地草稿 */
export interface CharacterDraft {
  description: string
  story_file: File | null
}

/** 创建角色返回的展开视图（解析后） */
export interface CharacterDetail {
  character: Character
  personalityParsed: PersonalityMap
  currentStateParsed: CurrentState
  speakingStyleList: string[]
  valuesList: string[]
  habitsList: string[]
}

/** 事件展示（含元数据） */
export interface EventView {
  id: number
  type: EventType
  content: string
  status: EventStatus
  timePeriod: TimePeriod | null
  orderIndex: number
  dayNumber: number
  resultJson: string | null
  createdAt: string
}
