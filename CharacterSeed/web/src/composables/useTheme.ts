/**
 * 主题管理 composable
 * - 支持 light / dark / auto 三种模式
 * - 持久化到 localStorage
 * - 跟 OS 偏好：auto 模式监听 prefers-color-scheme
 * - 立即应用主题（设置 document.documentElement.dataset.theme）
 *
 * 初始化顺序：useTheme() 在 main.ts 顶部调用 → 立即把主题写入 <html data-theme="...">
 * 避免页面闪白 / 闪黑（FOUC）。
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'auto'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'cs.theme'

/** 全局唯一 ref（多个组件共享） */
const mode = ref<ThemeMode>('auto')
const systemTheme = ref<ResolvedTheme>('light')

function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(m: ThemeMode) {
  if (typeof document === 'undefined') return
  const resolved: ResolvedTheme = m === 'auto' ? systemTheme.value : m
  document.documentElement.dataset.theme = resolved
}

function loadModeFromStorage(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'light' || v === 'dark' || v === 'auto') return v
  } catch { /* noop */ }
  return 'auto'
}

function persistMode(m: ThemeMode) {
  try { localStorage.setItem(STORAGE_KEY, m) } catch { /* noop */ }
}

// 监听 OS 主题变化
let mql: MediaQueryList | null = null
function onSystemChange(e: MediaQueryListEvent) {
  systemTheme.value = e.matches ? 'dark' : 'light'
  if (mode.value === 'auto') applyTheme('auto')
}

let initialized = false
function init() {
  if (initialized || typeof window === 'undefined') return
  initialized = true
  mode.value = loadModeFromStorage()
  systemTheme.value = getSystemTheme()
  applyTheme(mode.value)
  mql = window.matchMedia('(prefers-color-scheme: dark)')
  mql.addEventListener('change', onSystemChange)
}

// 模块加载即初始化（避免 FOUC）
init()

// 响应式跟随 mode 变化
watch(mode, (m) => {
  persistMode(m)
  applyTheme(m)
})

export function useTheme() {
  // 组件挂载时确保初始化（兜底：HMR 场景）
  onMounted(() => {
    if (!initialized) init()
  })
  onUnmounted(() => {
    // 不注销 mql：保留全局监听供后续组件使用
  })

  /** 解析后的最终主题 */
  const resolved = computed<ResolvedTheme>(() =>
    mode.value === 'auto' ? systemTheme.value : mode.value
  )

  function setMode(m: ThemeMode) {
    mode.value = m
  }

  function cycle() {
    // 在 light → dark → auto → light 之间循环
    const order: ThemeMode[] = ['light', 'dark', 'auto']
    const idx = order.indexOf(mode.value)
    mode.value = order[(idx + 1) % order.length]
  }

  return { mode, resolved, setMode, cycle }
}
