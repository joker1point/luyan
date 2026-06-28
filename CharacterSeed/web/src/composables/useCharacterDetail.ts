/**
 * 角色状态面板 composable
 * 加载记忆/对话/成长日志，并提供解析后的视图数据
 */
import { ref, watch } from 'vue'
import { memory as memoryApi, characters as charactersApi, ApiError } from '@/api'
import type { Character, MemoryResponse, ChatResponse, GrowthResponse, PersonalityMap, CurrentState } from '@/types'

function safeParse<T>(s: string | null | undefined, fallback: T): T {
  if (!s) return fallback
  try { return JSON.parse(s) as T } catch { return fallback }
}

export function useCharacterDetail(characterIdGetter: () => number | null) {
  const character = ref<Character | null>(null)
  const personalityParsed = ref<PersonalityMap>({})
  const currentStateParsed = ref<CurrentState>({})
  const speakingStyleList = ref<string[]>([])
  const valuesList = ref<string[]>([])
  const habitsList = ref<string[]>([])

  const memories = ref<MemoryResponse[]>([])
  const conversations = ref<ChatResponse[]>([])
  const growthLogs = ref<GrowthResponse[]>([])

  const loading = ref(false)
  const error = ref<string | null>(null)

  async function loadAll(id: number) {
    loading.value = true
    error.value = null
    try {
      const [char, mem, conv, logs] = await Promise.all([
        charactersApi.get(id),
        memoryApi.list(id, { limit: 100 }),
        memoryApi.conversations(id, { limit: 100 }),
        memoryApi.growthLogs(id, { limit: 50 }),
      ])
      character.value = char
      personalityParsed.value = safeParse<PersonalityMap>(char.personality, {})
      currentStateParsed.value = safeParse<CurrentState>(char.current_state, {})
      speakingStyleList.value = safeParse<string[]>(char.speaking_style, [])
      valuesList.value = safeParse<string[]>(char.values, [])
      habitsList.value = safeParse<string[]>(char.habits, [])
      memories.value = mem
      conversations.value = conv
      growthLogs.value = logs
    } catch (e) {
      error.value = e instanceof ApiError ? e.detail : (e as Error).message
    } finally {
      loading.value = false
    }
  }

  watch(
    () => characterIdGetter(),
    async (id) => {
      // 重置
      character.value = null
      personalityParsed.value = {}
      currentStateParsed.value = {}
      speakingStyleList.value = []
      valuesList.value = []
      habitsList.value = []
      memories.value = []
      conversations.value = []
      growthLogs.value = []
      if (id == null) return
      await loadAll(id)
    },
    { immediate: true }
  )

  async function refresh() {
    const id = characterIdGetter()
    if (id == null) return
    await loadAll(id)
  }

  return {
    character,
    personalityParsed,
    currentStateParsed,
    speakingStyleList,
    valuesList,
    habitsList,
    memories,
    conversations,
    growthLogs,
    loading,
    error,
    refresh,
  }
}
