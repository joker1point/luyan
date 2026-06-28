<template>
  <div class="status-view">
    <div v-if="!activeId" class="empty-state">
      <div style="font-size: 64px">📊</div>
      <div class="h2">请先选择角色</div>
      <router-link class="btn btn-primary" to="/create" style="margin-top: 12px">去创建 →</router-link>
    </div>

    <template v-else>
      <header class="page-header">
        <div>
          <div class="h1">📊 角色状态 · {{ character?.name }}</div>
          <div class="muted">
            Day {{ character?.day_number ?? 1 }} ·
            记忆 {{ memories.length }} 条 ·
            对话 {{ conversations.length }} 条 ·
            成长 {{ growthLogs.length }} 条
          </div>
        </div>
        <div class="row" style="gap: 8px">
          <button class="btn btn-ghost" @click="refresh" :disabled="loading">↻ 刷新</button>
          <button class="btn btn-danger btn-ghost" @click="onDelete">🗑 删除角色</button>
        </div>
      </header>

      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <div v-if="loading" class="tiny" style="padding: 20px; text-align: center">加载中…</div>

      <div v-else class="grid">
        <!-- 左列：人格 + 当前状态 -->
        <div class="col-left">
          <section class="card">
            <div class="card-title">🎭 6 维人格</div>
            <div class="personality-grid">
              <div v-for="[key, val] in personalityEntries" :key="key" class="personality-row">
                <div class="personality-label">
                  <span>{{ key }}</span>
                  <span class="personality-value">{{ val }}</span>
                </div>
                <div class="progress">
                  <div class="bar" :style="{ width: val + '%' }"></div>
                </div>
              </div>
            </div>
            <div v-if="personalityEntries.length === 0" class="muted" style="padding: 12px 0">
              尚未生成
            </div>
          </section>

          <section class="card">
            <div class="card-title">📍 当前状态</div>
            <div v-if="stateEntries.length" class="state-grid">
              <div v-for="[k, v] in stateEntries" :key="k" class="state-row">
                <span class="state-key">{{ k }}</span>
                <span class="state-val">{{ v }}</span>
              </div>
            </div>
            <div v-else class="muted">尚未设置</div>
          </section>

          <section class="card" v-if="character?.world_setting">
            <div class="card-title">🌍 世界设定</div>
            <div class="body-text">{{ character.world_setting }}</div>
          </section>

          <section class="card" v-if="character?.long_term_goal">
            <div class="card-title">🎯 长期目标</div>
            <div class="body-text">{{ character.long_term_goal }}</div>
          </section>
        </div>

        <!-- 右列：标签 + 记忆 + 对话 + 成长 -->
        <div class="col-right">
          <section class="card">
            <div class="card-title">🏷️ 角色画像</div>
            <div class="tag-block" v-if="speakingStyleList.length">
              <div class="tag-label">说话风格</div>
              <div class="row" style="flex-wrap: wrap; gap: 6px">
                <span v-for="s in speakingStyleList" :key="s" class="chip chip-info">{{ s }}</span>
              </div>
            </div>
            <div class="tag-block" v-if="valuesList.length">
              <div class="tag-label">核心信念</div>
              <div class="row" style="flex-wrap: wrap; gap: 6px">
                <span v-for="s in valuesList" :key="s" class="chip chip-accent">{{ s }}</span>
              </div>
            </div>
            <div class="tag-block" v-if="habitsList.length">
              <div class="tag-label">日常习惯</div>
              <div class="row" style="flex-wrap: wrap; gap: 6px">
                <span v-for="s in habitsList" :key="s" class="chip chip-success">{{ s }}</span>
              </div>
            </div>
            <div v-if="!speakingStyleList.length && !valuesList.length && !habitsList.length" class="muted">
              尚未生成
            </div>
          </section>

          <section class="card">
            <div class="card-title">
              <span>🧠 记忆</span>
              <div class="row" style="gap: 4px">
                <button
                  v-for="t in memoryTypes"
                  :key="t.value"
                  class="filter-btn"
                  :class="{ active: memoryFilter === t.value }"
                  @click="memoryFilter = t.value"
                >
                  {{ t.label }} ({{ t.count }})
                </button>
              </div>
            </div>
            <div v-if="filteredMemories.length === 0" class="muted">暂无该类型记忆</div>
            <div v-else class="memory-list">
              <div v-for="m in filteredMemories" :key="m.id" class="memory-item">
                <div class="row" style="justify-content: space-between">
                  <span class="chip" :class="memTypeClass(m.memory_type)">
                    {{ memTypeLabel(m.memory_type) }}
                  </span>
                  <span class="tiny">重要度 {{ m.importance }}/10</span>
                </div>
                <div class="memory-content">{{ m.content }}</div>
                <div class="tiny">{{ formatDate(m.created_at) }}</div>
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card-title">💬 对话历史（最近 20 条）</div>
            <div v-if="conversations.length === 0" class="muted">暂无对话</div>
            <div v-else class="conv-list">
              <div v-for="c in conversations.slice(0, 20)" :key="c.id" class="conv-item">
                <div class="conv-user">👤 {{ c.user_input }}</div>
                <div class="conv-ai">🤖 {{ c.npc_response }}</div>
                <div class="tiny">{{ formatDate(c.timestamp) }}</div>
              </div>
            </div>
          </section>

          <section class="card" v-if="growthLogs.length">
            <div class="card-title">🌱 成长记录</div>
            <div class="growth-list">
              <details v-for="g in growthLogs" :key="g.id" class="growth-item">
                <summary>
                  <span class="row" style="gap: 6px; align-items: center">
                    <span class="chip chip-accent">Day {{ extractDayFromCreated(g.created_at) }}</span>
                    <span class="growth-summary">{{ g.event_summary || '(无摘要)' }}</span>
                  </span>
                </summary>
                <div class="growth-body">
                  <div v-if="g.personality_delta">
                    <div class="growth-label">人格变化</div>
                    <pre class="meta-pre">{{ g.personality_delta }}</pre>
                  </div>
                  <div v-if="g.new_memories">
                    <div class="growth-label">新增记忆</div>
                    <pre class="meta-pre">{{ g.new_memories }}</pre>
                  </div>
                  <div v-if="g.world_changes_json">
                    <div class="growth-label">世界变化</div>
                    <pre class="meta-pre">{{ g.world_changes_json }}</pre>
                  </div>
                  <div v-if="g.schedule_json">
                    <div class="growth-label">次日日程</div>
                    <pre class="meta-pre">{{ g.schedule_json }}</pre>
                  </div>
                  <div v-if="g.growth_raw">
                    <details>
                      <summary>查看 Growth LLM 原始响应</summary>
                      <pre class="meta-pre">{{ g.growth_raw }}</pre>
                    </details>
                  </div>
                </div>
              </details>
            </div>
          </section>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useCharacters } from '@/composables/useCharacters'
import { useCharacterDetail } from '@/composables/useCharacterDetail'
import { characters as charactersApi } from '@/api'
import { formatDate } from '@/utils'
import { useRouter } from 'vue-router'
import type { MemoryType } from '@/types'

const { activeId } = useCharacters()
const router = useRouter()
const {
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
} = useCharacterDetail(() => activeId.value)

const memoryFilter = ref<MemoryType | 'all'>('all')

const memoryTypes = computed(() => [
  { value: 'all' as const,          label: '全部',     count: memories.value.length },
  { value: 'conversation' as const, label: '对话',     count: memories.value.filter(m => m.memory_type === 'conversation').length },
  { value: 'event' as const,        label: '事件',     count: memories.value.filter(m => m.memory_type === 'event').length },
  { value: 'growth' as const,       label: '成长',     count: memories.value.filter(m => m.memory_type === 'growth').length },
])

const filteredMemories = computed(() =>
  memoryFilter.value === 'all'
    ? memories.value
    : memories.value.filter(m => m.memory_type === memoryFilter.value)
)

const personalityEntries = computed(() =>
  Object.entries(personalityParsed.value).sort(([, a], [, b]) => Number(b) - Number(a))
)
const stateEntries = computed(() => Object.entries(currentStateParsed.value))

function memTypeLabel(t: MemoryType): string {
  switch (t) {
    case 'conversation': return '对话'
    case 'event':        return '事件'
    case 'growth':       return '成长'
    default: return t
  }
}
function memTypeClass(t: MemoryType): string {
  switch (t) {
    case 'conversation': return 'chip-info'
    case 'event':        return 'chip-warning'
    case 'growth':       return 'chip-accent'
    default: return ''
  }
}
function extractDayFromCreated(s: string): number {
  if (!character.value) return 0
  const created = new Date(character.value.created_at).getTime()
  const current = new Date(s).getTime()
  const dayMs = 24 * 60 * 60 * 1000
  return Math.max(1, Math.floor((current - created) / dayMs) + 1)
}

async function onDelete() {
  if (!activeId.value) return
  if (!window.confirm(`确定删除角色「${character.value?.name}」及其全部记忆/对话/成长记录？此操作不可恢复！`)) return
  try {
    await charactersApi.delete(activeId.value)
    window.alert('删除成功')
    // 清除激活
    const { setActive } = useCharacters()
    setActive(null)
    router.push('/create')
  } catch (e) {
    window.alert('删除失败：' + (e as Error).message)
  }
}
</script>

<style scoped>
.status-view {
  padding: 22px 28px;
  overflow-y: auto;
  flex: 1;
}
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 80px 40px;
  text-align: center;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: 18px;
  align-items: start;
}
@media (max-width: 980px) {
  .grid { grid-template-columns: 1fr; }
}
.col-left, .col-right {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.card-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text);
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.body-text {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--text);
  white-space: pre-wrap;
  background: var(--bg-soft);
  padding: 10px 12px;
  border-radius: var(--radius-sm);
}

.personality-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.personality-row {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.personality-label {
  display: flex;
  justify-content: space-between;
  font-size: 12.5px;
  color: var(--text);
}
.personality-value {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--accent);
}

.state-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 8px;
}
.state-row {
  display: flex;
  flex-direction: column;
  background: var(--bg-soft);
  padding: 8px 10px;
  border-radius: var(--radius-sm);
}
.state-key {
  font-size: 11px;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.state-val {
  font-size: 13px;
  color: var(--text);
  font-weight: 500;
}

.tag-block { margin-bottom: 12px; }
.tag-block:last-child { margin-bottom: 0; }
.tag-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 6px;
}

.filter-btn {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 999px;
  color: var(--text-secondary);
  background: var(--bg-soft);
  border: 1px solid transparent;
}
.filter-btn:hover { background: var(--bg-hover); }
.filter-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.memory-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow-y: auto;
}
.memory-item {
  padding: 10px 12px;
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.memory-content {
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--text);
}

.conv-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 400px;
  overflow-y: auto;
}
.conv-item {
  padding: 10px 12px;
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.conv-user, .conv-ai {
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
}

.growth-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 500px;
  overflow-y: auto;
}
.growth-item {
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
}
.growth-item summary {
  cursor: pointer;
  user-select: none;
}
.growth-summary {
  font-size: 13px;
  color: var(--text);
}
.growth-body {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.growth-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 4px;
}
.meta-pre {
  font-family: var(--font-mono);
  font-size: 11.5px;
  background: var(--bg-card);
  padding: 8px 10px;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 180px;
  overflow-y: auto;
}
</style>
