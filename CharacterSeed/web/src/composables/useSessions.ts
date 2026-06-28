/**
 * 会话管理 composable
 * 按角色维护会话列表，支持创建/重命名/删除/切换
 */
import { ref, computed, watch } from 'vue'
import { sessions as sessionsApi, ApiError } from '@/api'
import type { ChatSessionInfo, ChatSessionWithMessages } from '@/types'

export function useSessions(characterIdGetter: () => number | null) {
  const sessions = ref<ChatSessionInfo[]>([])
  const activeSessionId = ref<number | null>(null)
  const currentSessionDetail = ref<ChatSessionWithMessages | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  /** 当角色变化时自动重置 + 加载 */
  watch(
    () => characterIdGetter(),
    async (id) => {
      activeSessionId.value = null
      currentSessionDetail.value = null
      sessions.value = []
      if (id != null) {
        await refresh()
      }
    },
    { immediate: true }
  )

  async function refresh() {
    const id = characterIdGetter()
    if (id == null) return
    loading.value = true
    error.value = null
    try {
      sessions.value = await sessionsApi.list(id)
    } catch (e) {
      error.value = e instanceof ApiError ? e.detail : (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function createNew(title?: string) {
    const id = characterIdGetter()
    if (id == null) throw new Error('未选择角色')
    const session = await sessionsApi.create({ character_id: id, title: title ?? null })
    sessions.value.unshift(session)
    activeSessionId.value = session.id
    return session
  }

  async function select(id: number) {
    activeSessionId.value = id
    await loadDetail()
  }

  async function loadDetail() {
    if (activeSessionId.value == null) {
      currentSessionDetail.value = null
      return
    }
    try {
      currentSessionDetail.value = await sessionsApi.get(activeSessionId.value)
    } catch (e) {
      error.value = e instanceof ApiError ? e.detail : (e as Error).message
      currentSessionDetail.value = null
    }
  }

  async function rename(id: number, title: string) {
    const updated = await sessionsApi.rename(id, title)
    const idx = sessions.value.findIndex(s => s.id === id)
    if (idx >= 0) sessions.value[idx] = updated
  }

  async function remove(id: number) {
    await sessionsApi.delete(id)
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (activeSessionId.value === id) {
      activeSessionId.value = null
      currentSessionDetail.value = null
    }
  }

  const activeSession = computed(() =>
    activeSessionId.value == null
      ? null
      : sessions.value.find(s => s.id === activeSessionId.value) ?? null
  )

  return {
    sessions,
    activeSessionId,
    activeSession,
    currentSessionDetail,
    loading,
    error,
    refresh,
    createNew,
    select,
    loadDetail,
    rename,
    remove,
  }
}
