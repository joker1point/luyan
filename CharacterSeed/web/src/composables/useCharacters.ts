/**
 * 角色管理 composable
 * 维护角色列表、当前选中角色、刷新与删除
 */
import { ref, computed } from 'vue'
import { characters as charactersApi, ApiError } from '@/api'
import type { Character } from '@/types'

const characters = ref<Character[]>([])
const activeId = ref<number | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    characters.value = await charactersApi.list()
  } catch (e) {
    error.value = e instanceof ApiError ? e.detail : (e as Error).message
  } finally {
    loading.value = false
  }
}

async function loadById(id: number) {
  const c = await charactersApi.get(id)
  const idx = characters.value.findIndex(x => x.id === id)
  if (idx >= 0) characters.value[idx] = c
  else characters.value.push(c)
  return c
}

async function remove(id: number) {
  const detail = await charactersApi.delete(id)
  characters.value = characters.value.filter(c => c.id !== id)
  if (activeId.value === id) activeId.value = null
  return detail
}

function setActive(id: number | null) {
  activeId.value = id
  // 持久化到 localStorage
  if (id != null) localStorage.setItem('cs.activeCharacterId', String(id))
  else localStorage.removeItem('cs.activeCharacterId')
}

/** 当前激活的角色对象 */
const active = computed<Character | null>(() =>
  activeId.value == null
    ? null
    : characters.value.find(c => c.id === activeId.value) ?? null
)

/** 应用启动时尝试从 localStorage 恢复选中 */
function bootstrapFromStorage() {
  const saved = localStorage.getItem('cs.activeCharacterId')
  if (saved) {
    const n = Number(saved)
    if (Number.isFinite(n)) activeId.value = n
  }
}

export function useCharacters() {
  return {
    characters,
    activeId,
    active,
    loading,
    error,
    refresh,
    loadById,
    remove,
    setActive,
    bootstrapFromStorage,
  }
}
