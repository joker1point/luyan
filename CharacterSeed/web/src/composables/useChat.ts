/**
 * 对话 composable
 * 维护当前会话的消息列表、发送消息、加载历史
 *
 * 发送消息走流式接口（/api/chat/stream），实现打字机效果：
 *   - meta 事件到达后更新 sessionId / sessionTitle
 *   - speech 事件增量追加到占位消息的 content
 *   - done 事件到达后用完整数据替换占位（补齐 action/expression/raw 等字段）
 */
import { ref, computed } from 'vue'
import { chat as chatApi, sessions as sessionsApi, ApiError } from '@/api'
import type { ChatMessageItem, ChatResponse, ChatSessionWithMessages } from '@/types'

/** 将后端 ChatResponse 转成前端 ChatMessageItem（拆为两条） */
function toMessages(r: ChatResponse): ChatMessageItem[] {
  return [
    {
      id: r.id * 10,
      role: 'user',
      content: r.user_input,
      timestamp: r.timestamp,
    },
    {
      id: r.id,
      role: 'assistant',
      content: r.npc_response,
      emotion: r.emotion,
      action: r.action,
      expression: r.expression,
      director_raw: r.director_raw,
      actor_raw: r.actor_raw,
      timestamp: r.timestamp,
    },
  ]
}

export function useChat() {
  const messages = ref<ChatMessageItem[]>([])
  const sessionId = ref<number | null>(null)
  const sessionTitle = ref<string | null>(null)
  const sending = ref(false)
  const error = ref<string | null>(null)

  /** 加载某个 session 的全部历史 */
  async function loadSession(id: number) {
    sessionId.value = id
    error.value = null
    try {
      const detail: ChatSessionWithMessages = await sessionsApi.get(id)
      sessionTitle.value = detail.title
      messages.value = detail.messages.flatMap(toMessages)
    } catch (e) {
      error.value = e instanceof ApiError ? e.detail : (e as Error).message
      messages.value = []
    }
  }

  /** 发送消息（流式，自动建 session 当 sessionId 为 null） */
  async function send(characterId: number, text: string) {
    const trimmed = text.trim()
    if (!trimmed || sending.value) return null
    error.value = null

    // 1. 乐观插入 user 消息
    const tempId = -Date.now()
    messages.value.push({
      id: tempId,
      role: 'user',
      content: trimmed,
      timestamp: new Date().toISOString(),
    })

    // 2. 占位 assistant（pending 状态，content 随 speech 增量增长）
    const placeholderId = tempId - 1
    messages.value.push({
      id: placeholderId,
      role: 'assistant',
      content: '',
      pending: true,
      thinking_phase: 'starting',
      thinking_message: '正在处理请求…',
      timestamp: new Date().toISOString(),
    })

    // 通过 reactive 代理访问占位消息，确保增量修改能触发视图更新
    const getPlaceholder = () => messages.value.find(m => m.id === placeholderId)

    sending.value = true
    try {
      await chatApi.streamSend(
        {
          character_id: characterId,
          message: trimmed,
          session_id: sessionId.value,
        },
        {
          // 阶段通知：实时更新占位消息的 thinking_phase（消除"点了发送却没反应"的卡顿感）
          onThinking: (data) => {
            const ph = getPlaceholder()
            if (!ph) return
            ph.thinking_phase = data.phase
            ph.thinking_message = data.message ?? ''
            // cache_hit 阶段意味着马上有 speech，可以提前把 pending 改掉
            if (data.phase === 'cache_hit') {
              ph.pending = false
            }
          },
          // meta 到达：立即更新 session 信息（侧栏可同步）
          onMeta: (data) => {
            sessionId.value = data.session_id
            sessionTitle.value = data.session_title
            const ph = getPlaceholder()
            if (ph) {
              // Director 的情绪标签可以提前显示
              ph.emotion = data.emotion
              ph.director_raw = data.director_raw
              ph.thinking_phase = 'directing_done'
              ph.thinking_message = ''
            }
          },
          // speech 增量：追加到占位消息，实现打字机效果
          onSpeech: (delta) => {
            const ph = getPlaceholder()
            if (!ph) return
            ph.content += delta
            // 收到首段文本后取消 pending 动画，改为显示文本
            if (ph.pending && ph.content) {
              ph.pending = false
            }
          },
          // done：用完整数据替换占位（补齐 action/expression/actor_raw/id 等）
          onDone: (resp) => {
            sessionId.value = resp.session_id
            sessionTitle.value = resp.session_title
            // 移除占位
            messages.value = messages.value.filter(m => m.id !== placeholderId)
            // 追加真实两条（user + assistant）
            messages.value.push(...toMessages(resp))
          },
          onError: (msg) => {
            error.value = msg
            const ph = getPlaceholder()
            if (ph) {
              ph.error = msg
              ph.pending = false
              if (!ph.content) {
                ph.content = `[错误] ${msg}`
              }
            }
          },
        },
      )
      return null
    } finally {
      sending.value = false
    }
  }

  function clear() {
    messages.value = []
    sessionId.value = null
    sessionTitle.value = null
    error.value = null
  }

  const hasMessages = computed(() => messages.value.length > 0)

  return {
    messages,
    sessionId,
    sessionTitle,
    sending,
    error,
    hasMessages,
    loadSession,
    send,
    clear,
  }
}
