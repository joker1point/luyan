/**
 * 事件推进 composable
 * 维护事件列表、推进单个事件、日迭代、一键推演
 */
import { ref, computed, watch } from 'vue'
import { events as eventsApi, ApiError } from '@/api'
import type { EventResponse, IterateResponse, AutoResponse, EventStatus } from '@/types'

export function useEvents(characterIdGetter: () => number | null) {
  const events = ref<EventResponse[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastIterateResult = ref<IterateResponse | null>(null)
  const lastAutoResult = ref<AutoResponse | null>(null)
  const operating = ref(false)

  watch(
    () => characterIdGetter(),
    async (id) => {
      events.value = []
      lastIterateResult.value = null
      lastAutoResult.value = null
      if (id != null) await refresh()
    },
    { immediate: true }
  )

  async function refresh(filter?: { day_number?: number; status?: EventStatus }) {
    const id = characterIdGetter()
    if (id == null) return
    loading.value = true
    error.value = null
    try {
      events.value = await eventsApi.list(id, filter)
    } catch (e) {
      error.value = e instanceof ApiError ? e.detail : (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function advance() {
    const id = characterIdGetter()
    if (id == null || operating.value) return null
    operating.value = true
    error.value = null
    try {
      const updated = await eventsApi.advance(id)
      const idx = events.value.findIndex(e => e.id === updated.id)
      if (idx >= 0) events.value[idx] = updated
      else events.value.push(updated)
      return updated
    } catch (e) {
      error.value = e instanceof ApiError ? e.detail : (e as Error).message
      return null
    } finally {
      operating.value = false
    }
  }

  async function iterate() {
    const id = characterIdGetter()
    if (id == null || operating.value) return null
    operating.value = true
    error.value = null
    try {
      const result = await eventsApi.iterate(id)
      lastIterateResult.value = result
      // 拉取新一天的事件
      await refresh()
      return result
    } catch (e) {
      error.value = e instanceof ApiError ? e.detail : (e as Error).message
      return null
    } finally {
      operating.value = false
    }
  }

  async function auto() {
    const id = characterIdGetter()
    if (id == null || operating.value) return null
    operating.value = true
    error.value = null
    try {
      const result = await eventsApi.auto(id)
      lastAutoResult.value = result
      await refresh()
      return result
    } catch (e) {
      error.value = e instanceof ApiError ? e.detail : (e as Error).message
      return null
    } finally {
      operating.value = false
    }
  }

  /** 按 day_number 分组 */
  const eventsByDay = computed(() => {
    const map = new Map<number, EventResponse[]>()
    for (const ev of events.value) {
      const arr = map.get(ev.day_number) ?? []
      arr.push(ev)
      map.set(ev.day_number, arr)
    }
    return Array.from(map.entries())
      .sort(([a], [b]) => a - b)
      .map(([day, list]) => ({ day, list: list.sort((a, b) => a.order_index - b.order_index) }))
  })

  /** 当前天的 pending 数量 */
  const pendingCount = computed(() =>
    events.value.filter(e => e.status === 'pending').length
  )

  return {
    events,
    eventsByDay,
    pendingCount,
    loading,
    error,
    operating,
    lastIterateResult,
    lastAutoResult,
    refresh,
    advance,
    iterate,
    auto,
  }
}
