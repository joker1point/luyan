/**
 * CharacterSeed Web — REST API 客户端
 * 基于 fetch 封装，覆盖 API 参考文档的 26 个端点
 */
import type {
  Character, ChatRequest, ChatResponse,
  ChatSessionInfo, ChatSessionCreate, ChatSessionUpdate, ChatSessionWithMessages,
  GrowthTriggerRequest, GrowthResponse,
  AdvanceRequest, IterateRequest, IterateResponse, AutoResponse, EventResponse,
  MemoryResponse, MemoryType,
  LLMSettingsResponse, LLMUpdateRequest, LLMTestRequest, LLMTestResponse,
  ModelsListResponse, LatencyTestRequest, LatencyTestResponse,
} from '@/types'

/**
 * API base URL —— 直连 FastAPI 后端（不走 Vite 代理）
 *
 * 决策说明：
 *   原来走 Vite /api 代理是考虑同源开发，但 Vite 的 http-proxy
 *   对 SSE（流式聊天）存在背压问题——proxyRes.pipe(res) 会让
 *   客户端（浏览器）慢读时阻塞上游 read，导致后端 LLM 流式
 *   httpx.Client 读超时报 Connection error，最终流式 chat 全部
 *   走 fallback，5 轮对话 100% 失败。
 *
 *   直连后端：
 *     - SSE 流式无中间管道，httpx 读不会被客户端慢读阻塞
 *     - 跨域已被后端 CORSMiddleware 允许（http://localhost:5173）
 *     - 部署时把这里的 baseURL 换成生产域名即可
 */
export const BASE = (typeof window !== 'undefined' && window.location.hostname === 'localhost' && window.location.port === '5173')
  ? 'http://localhost:8000/api'   // dev: 直连后端
  : '/api'                        // prod: 走同源反向代理

// 旧版常量（保留兼容）：const BASE = '/api'

/**
 * 统一 fetch 包装：
 *   - 自动加 base
 *   - JSON 序列化
 *   - 非 2xx 抛出 ApiError（detail 取后端 detail 字段）
 *   - 超时：默认 30s
 */
class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(`[${status}] ${detail}`)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number }
): Promise<T> {
  const { timeoutMs = 30000, ...rest } = init ?? {}
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)

  try {
    const resp = await fetch(`${BASE}${path}`, {
      ...rest,
      signal: ctrl.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(rest.headers ?? {}),
      },
    })
    const text = await resp.text()
    let data: any = null
    if (text) {
      try { data = JSON.parse(text) } catch { data = text }
    }
    if (!resp.ok) {
      const detail = (data && typeof data === 'object' && 'detail' in data)
        ? String(data.detail)
        : `HTTP ${resp.status}`
      throw new ApiError(resp.status, detail)
    }
    return data as T
  } catch (e) {
    if (e instanceof ApiError) throw e
    if ((e as Error).name === 'AbortError') {
      throw new ApiError(0, '请求超时')
    }
    throw new ApiError(0, (e as Error).message || '网络错误')
  } finally {
    clearTimeout(timer)
  }
}

/** multipart 专用：用于上传 TXT */
async function uploadForm<T>(path: string, form: FormData): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    body: form,
  })
  const text = await resp.text()
  let data: any = null
  if (text) {
    try { data = JSON.parse(text) } catch { data = text }
  }
  if (!resp.ok) {
    const detail = (data && typeof data === 'object' && 'detail' in data)
      ? String(data.detail) : `HTTP ${resp.status}`
    throw new ApiError(resp.status, detail)
  }
  return data as T
}

export { ApiError }

// ============================================================================
// 1. 角色管理
// ============================================================================

export const characters = {
  list: (params?: { skip?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.skip != null) q.set('skip', String(params.skip))
    if (params?.limit != null) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request<Character[]>(`/characters${qs ? '?' + qs : ''}`)
  },

  get: (id: number) => request<Character>(`/characters/${id}`),

  /** 用 description 字符串创建 */
  createByText: (description: string) => {
    const fd = new FormData()
    fd.append('description', description)
    return uploadForm<Character>('/characters/create', fd)
  },

  /** 用 TXT 文件创建（可附 description 追加描述） */
  createByFile: (file: File, extraDescription?: string) => {
    const fd = new FormData()
    fd.append('story_file', file)
    if (extraDescription) fd.append('description', extraDescription)
    return uploadForm<Character>('/characters/create', fd)
  },

  delete: (id: number) => request<{ detail: string }>(`/characters/${id}`, { method: 'DELETE' }),
}

// ============================================================================
// 2. 对话交互
// ============================================================================

/** 流式事件回调集合 */
export interface StreamCallbacks {
  /** 管线阶段通知（请求被接收后立即触发，让前端可立刻显示"思考中"占位） */
  onThinking?: (data: {
    phase: 'starting' | 'directing' | 'acting' | 'cache_hit' | string
    message?: string
    started_at_ms?: number
  }) => void
  /** Director 完成、session 建好后触发（前端可立即更新侧栏） */
  onMeta?: (data: {
    session_id: number
    session_title: string
    emotion: string
    director_raw: string | null
  }) => void
  /** speech 文本增量（每收到一段就触发，实现打字机效果） */
  onSpeech?: (delta: string) => void
  /** 流结束，拿到完整 ChatResponse */
  onDone?: (data: ChatResponse) => void
  /** 任意错误（含降级提示） */
  onError?: (message: string) => void
}

export const chat = {
  send: (body: ChatRequest) =>
    request<ChatResponse>('/chat', { method: 'POST', body: JSON.stringify(body) }),

  /**
   * 流式发送对话（SSE）。
   *
   * 使用 fetch + ReadableStream 手动解析 text/event-stream，
   * 而非 EventSource —— 因为 EventSource 不支持 POST 请求体。
   *
   * 超时：流式场景默认 120s（LLM 长文本生成需要时间）
   */
  streamSend: async (body: ChatRequest, cb: StreamCallbacks): Promise<void> => {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 120000)

    try {
      const resp = await fetch(`${BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      })

      if (!resp.ok) {
        const text = await resp.text()
        let detail = `HTTP ${resp.status}`
        try {
          const j = JSON.parse(text)
          if (j?.detail) detail = String(j.detail)
        } catch { /* ignore */ }
        cb.onError?.(detail)
        return
      }

      if (!resp.body) {
        cb.onError?.('响应体为空，浏览器不支持流式读取')
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      // SSE 事件以 \n\n 分隔；逐块读取并解析
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按空行（\n\n）切分出完整事件
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, sep)
          buffer = buffer.slice(sep + 2)

          const parsed = parseSSE(rawEvent)
          if (!parsed) continue

          switch (parsed.event) {
            case 'thinking':
              cb.onThinking?.(parsed.data)
              break
            case 'meta':
              cb.onMeta?.(parsed.data)
              break
            case 'speech':
              cb.onSpeech?.(parsed.data?.text ?? '')
              break
            case 'done':
              cb.onDone?.(parsed.data as ChatResponse)
              break
            case 'error':
              cb.onError?.(parsed.data?.message ?? '未知错误')
              break
          }
        }
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        cb.onError?.('请求超时')
      } else {
        cb.onError?.((e as Error).message || '网络错误')
      }
    } finally {
      clearTimeout(timer)
    }
  },
}

/** 解析单个 SSE 事件块（形如 "event: xxx\ndata: {...}"） */
function parseSSE(raw: string): { event: string; data: any } | null {
  const lines = raw.split('\n')
  let event = 'message'
  let dataStr = ''
  for (const line of lines) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataStr += line.slice(5).trim()
    }
  }
  if (!dataStr) return null
  try {
    return { event, data: JSON.parse(dataStr) }
  } catch {
    return { event, data: dataStr }
  }
}

// ============================================================================
// 3. 会话管理
// ============================================================================

export const sessions = {
  list: (characterId: number, params?: { search?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams({ character_id: String(characterId) })
    if (params?.search) q.set('search', params.search)
    if (params?.limit != null) q.set('limit', String(params.limit))
    if (params?.offset != null) q.set('offset', String(params.offset))
    return request<ChatSessionInfo[]>(`/sessions?${q.toString()}`)
  },

  create: (body: ChatSessionCreate) =>
    request<ChatSessionInfo>('/sessions', { method: 'POST', body: JSON.stringify(body) }),

  get: (id: number) =>
    request<ChatSessionWithMessages>(`/sessions/${id}`),

  rename: (id: number, title: string) =>
    request<ChatSessionInfo>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title } satisfies ChatSessionUpdate),
    }),

  delete: (id: number) =>
    request<{ deleted: boolean; session_id: number }>(`/sessions/${id}`, { method: 'DELETE' }),
}

// ============================================================================
// 4. 成长系统
// ============================================================================

export const growth = {
  trigger: (character_id: number) =>
    request<GrowthResponse>('/growth/trigger', {
      method: 'POST',
      body: JSON.stringify({ character_id } satisfies GrowthTriggerRequest),
    }),
}

// ============================================================================
// 5. 事件推进
// ============================================================================

export const events = {
  list: (characterId: number, params?: { day_number?: number; status?: string }) => {
    const q = new URLSearchParams()
    if (params?.day_number != null) q.set('day_number', String(params.day_number))
    if (params?.status) q.set('status', params.status)
    const qs = q.toString()
    return request<EventResponse[]>(`/characters/${characterId}/events${qs ? '?' + qs : ''}`)
  },

  advance: (character_id: number) =>
    request<EventResponse>('/event/advance', {
      method: 'POST',
      body: JSON.stringify({ character_id } satisfies AdvanceRequest),
    }),

  iterate: (character_id: number) =>
    request<IterateResponse>('/time/iterate', {
      method: 'POST',
      body: JSON.stringify({ character_id } satisfies IterateRequest),
    }),

  auto: (character_id: number) =>
    request<AutoResponse>('/time/auto', {
      method: 'POST',
      body: JSON.stringify({ character_id } satisfies AdvanceRequest),
    }),
}

// ============================================================================
// 6. 角色数据查询
// ============================================================================

export const memory = {
  list: (characterId: number, params?: { memory_type?: MemoryType; skip?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.memory_type) q.set('memory_type', params.memory_type)
    if (params?.skip != null) q.set('skip', String(params.skip))
    if (params?.limit != null) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request<MemoryResponse[]>(`/characters/${characterId}/memories${qs ? '?' + qs : ''}`)
  },

  conversations: (characterId: number, params?: { skip?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.skip != null) q.set('skip', String(params.skip))
    if (params?.limit != null) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request<ChatResponse[]>(`/characters/${characterId}/conversations${qs ? '?' + qs : ''}`)
  },

  growthLogs: (characterId: number, params?: { skip?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.skip != null) q.set('skip', String(params.skip))
    if (params?.limit != null) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request<GrowthResponse[]>(`/characters/${characterId}/growth-logs${qs ? '?' + qs : ''}`)
  },
}

// ============================================================================
// 7. LLM 设置
// ============================================================================

export const llmSettings = {
  get: () => request<LLMSettingsResponse>('/settings/llm'),

  providers: () =>
    request<{
      providers: { id: string; name: string; needs_key: 'true' | 'false' }[]
      defaults: Record<string, { base_url: string; model: string }>
    }>('/settings/llm/providers'),

  update: (body: LLMUpdateRequest) =>
    request<LLMSettingsResponse>('/settings/llm', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  test: (body: LLMTestRequest = {}) =>
    request<LLMTestResponse>('/settings/llm/test', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

// ============================================================================
// 8. API 工具
// ============================================================================

export const testTools = {
  listModels: (params?: { provider_id?: string; base_url?: string; api_key?: string }) => {
    const q = new URLSearchParams()
    if (params?.provider_id) q.set('provider_id', params.provider_id)
    if (params?.base_url) q.set('base_url', params.base_url)
    if (params?.api_key) q.set('api_key', params.api_key)
    const qs = q.toString()
    return request<ModelsListResponse>(`/test/models${qs ? '?' + qs : ''}`)
  },

  latency: (body: LatencyTestRequest = {}) =>
    request<LatencyTestResponse>('/test/latency', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

// ============================================================================
// 9. 系统
// ============================================================================

export const system = {
  health: () => request<{ message: string; docs: string; version: string }>('/'),
}

/** 默认导出：聚合所有模块 */
export const api = {
  characters,
  chat,
  sessions,
  growth,
  events,
  memory,
  llmSettings,
  testTools,
  system,
}

export default api
