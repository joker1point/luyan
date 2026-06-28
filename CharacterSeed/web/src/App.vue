<template>
  <div class="app-layout">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-logo">CS</div>
        <div class="brand-name">CharacterSeed</div>
        <div class="brand-sub">AI 数字生命</div>
      </div>

      <nav class="nav">
        <router-link to="/create" class="nav-item" active-class="active">
          <span class="nav-icon">＋</span> 角色创建
        </router-link>
        <router-link to="/chat" class="nav-item" active-class="active">
          <span class="nav-icon">💬</span> 对话
        </router-link>
        <router-link to="/events" class="nav-item" active-class="active">
          <span class="nav-icon">📅</span> 事件推进
        </router-link>
        <router-link to="/status" class="nav-item" active-class="active">
          <span class="nav-icon">📊</span> 状态面板
        </router-link>
        <router-link to="/settings" class="nav-item" active-class="active">
          <span class="nav-icon">⚙</span> LLM 设置
        </router-link>
      </nav>

      <div class="character-section">
        <div class="section-label">当前角色</div>
        <div v-if="loadingChars" class="empty-sm">加载中…</div>
        <div v-else-if="characters.length === 0" class="empty-sm">
          还没有角色，<router-link to="/create">立即创建</router-link>
        </div>
        <div v-else class="char-list">
          <button
            v-for="c in characters"
            :key="c.id"
            class="char-card"
            :class="{ active: activeId === c.id }"
            @click="selectChar(c.id)"
          >
            <div class="char-name">{{ c.name }}</div>
            <div class="char-day">Day {{ c.day_number }}</div>
          </button>
        </div>
      </div>

      <div class="footer">
        <div class="health-pill" :class="backendOk ? 'ok' : 'err'">
          <span class="dot"></span>
          {{ backendOk ? '后端已连接' : '后端未连接' }}
        </div>
        <div class="spacer"></div>
        <button
          class="theme-toggle"
          :class="`is-${mode}`"
          :title="`主题：${themeLabel}`"
          @click="cycleTheme"
        >
          <span class="theme-icon" v-if="resolved === 'light'">☀</span>
          <span class="theme-icon" v-else>☾</span>
          <span class="theme-label">{{ themeLabel }}</span>
        </button>
        <button v-if="activeId" class="btn btn-ghost btn-sm" @click="refreshActive">
          ↻
        </button>
      </div>
    </aside>

    <main class="main">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useCharacters } from '@/composables/useCharacters'
import { useTheme, type ThemeMode } from '@/composables/useTheme'
import { system, ApiError } from '@/api'

const {
  characters,
  activeId,
  loading: loadingChars,
  refresh: refreshChars,
  setActive,
  bootstrapFromStorage,
} = useCharacters()

const { mode, resolved, cycle: cycleTheme } = useTheme()

const themeLabel = computed<string>(() => {
  if (mode.value === 'light') return '浅色'
  if (mode.value === 'dark') return '深色'
  return '自动'
})

const backendOk = ref(false)

async function checkBackend() {
  try {
    await system.health()
    backendOk.value = true
  } catch {
    backendOk.value = false
  }
}

function selectChar(id: number) {
  setActive(id)
}

async function refreshActive() {
  await refreshChars()
}

onMounted(async () => {
  bootstrapFromStorage()
  await refreshChars()
  await checkBackend()
  // 心跳：每 30s 探一次
  setInterval(checkBackend, 30000)
})
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100%;
}

.sidebar {
  width: 240px;
  min-width: 240px;
  background: var(--bg-card);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 22px 18px;
  gap: 18px;
  overflow: hidden;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 4px;
}
.brand-logo {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--accent);
  color: var(--accent-text);
  font-weight: 700;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-sm);
  letter-spacing: -0.5px;
}
.brand-name {
  font-weight: 700;
  font-size: 15px;
  color: var(--text);
  letter-spacing: -0.2px;
}
.brand-sub {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: -2px;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 13.5px;
  transition: background 0.15s, color 0.15s;
}
.nav-item:hover {
  background: var(--bg-soft);
  color: var(--text);
  text-decoration: none;
}
.nav-item.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.nav-icon {
  display: inline-flex;
  width: 20px;
  justify-content: center;
}

.character-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-height: 0;
}
.section-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 0 4px;
}
.empty-sm {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 10px 4px;
  line-height: 1.6;
}
.char-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
  padding-right: 2px;
}
.char-card {
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  transition: background 0.12s, border-color 0.12s;
  width: 100%;
}
.char-card:hover {
  background: var(--bg-soft);
  border-color: var(--border);
}
.char-card.active {
  background: var(--accent-soft);
  border-color: var(--accent);
}
.char-name {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.char-card.active .char-name { color: var(--accent); font-weight: 600; }
.char-day {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}
.health-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
}
.health-pill .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.health-pill.ok { background: var(--success-soft); color: var(--success); }
.health-pill.ok .dot { background: var(--success); }
.health-pill.err { background: var(--danger-soft); color: var(--danger); }
.health-pill.err .dot { background: var(--danger); }

/* 主题切换按钮 —— 极简图标按钮 */
.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 500;
  background: var(--bg-soft);
  color: var(--text-secondary);
  border: 1px solid var(--border);
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.theme-toggle:hover {
  background: var(--bg-hover);
  color: var(--text);
  border-color: var(--border-strong);
}
.theme-toggle.is-dark {
  color: var(--warning);
  background: var(--warning-soft);
  border-color: transparent;
}
.theme-toggle.is-auto {
  color: var(--info);
  background: var(--info-soft);
  border-color: transparent;
}
.theme-icon {
  font-size: 13px;
  line-height: 1;
}
.theme-label {
  font-size: 11px;
  letter-spacing: 0.2px;
}

.main {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  background: var(--bg);
  display: flex;
  flex-direction: column;
}
</style>
