<template>
  <div class="events-view">
    <div v-if="!activeId" class="empty-state">
      <div style="font-size: 64px">📅</div>
      <div class="h2">请先选择角色</div>
      <router-link class="btn btn-primary" to="/create" style="margin-top: 12px">去创建 →</router-link>
    </div>

    <template v-else>
      <header class="page-header">
        <div>
          <div class="h1">📅 事件推进 · {{ activeName }}</div>
          <div class="muted">
            当前 Day {{ activeDay }} · 共 {{ events.length }} 个事件
            <span v-if="pendingCount > 0" class="chip chip-warning">
              {{ pendingCount }} 个待推进
            </span>
          </div>
        </div>

        <div class="row" style="gap: 8px">
          <button
            class="btn btn-ghost"
            @click="refresh()"
            :disabled="loading"
          >
            ↻ 刷新
          </button>
          <button
            class="btn btn-primary"
            @click="onAdvance"
            :disabled="operating || pendingCount === 0"
          >
            推进一个 →
          </button>
          <button
            class="btn btn-primary"
            @click="onIterate"
            :disabled="operating"
          >
            迭代到下一天 ⏭
          </button>
          <button
            class="btn btn-ghost"
            @click="onAuto"
            :disabled="operating"
            title="串联：推进全部 pending → 自动迭代到下一天"
          >
            一键推演 ⚡
          </button>
        </div>
      </header>

      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <div v-if="loading && events.length === 0" class="empty">加载中…</div>
      <div v-else-if="events.length === 0" class="empty">
        <div style="font-size: 48px">📭</div>
        <div>该角色暂无事件</div>
        <div class="tiny">创建角色后会自动生成 Day 1 事件</div>
      </div>

      <div v-else class="timeline">
        <div v-for="group in eventsByDay" :key="group.day" class="day-block">
          <div class="day-header">
            <div class="day-num">Day {{ group.day }}</div>
            <div class="day-stats">
              <span class="chip chip-warning">{{ group.list.filter(e => e.status === 'pending').length }} 待</span>
              <span class="chip chip-success">{{ group.list.filter(e => e.status === 'completed').length }} 完成</span>
            </div>
          </div>

          <div class="event-list">
            <div
              v-for="ev in group.list"
              :key="ev.id"
              class="event-card"
              :class="`status-${ev.status}`"
            >
              <div class="event-rail" :style="{ background: timePeriodVar(ev.time_period) }"></div>
              <div class="event-main">
                <div class="event-top">
                  <div class="row" style="gap: 6px; flex-wrap: wrap">
                    <span class="chip" :style="{
                      background: 'transparent',
                      color: timePeriodVar(ev.time_period),
                      borderColor: 'transparent',
                    }">
                      {{ timePeriodLabel(ev.time_period) }}
                    </span>
                    <span class="chip">{{ eventTypeLabel(ev.event_type) }}</span>
                    <span class="chip" :style="{
                      background: 'transparent',
                      color: statusVar(ev.status),
                      borderColor: 'transparent',
                    }">
                      {{ statusLabel(ev.status) }}
                    </span>
                    <span class="tiny">#{{ ev.order_index }}</span>
                  </div>
                </div>

                <div class="event-content">{{ ev.content }}</div>

                <div v-if="ev.result_json" class="event-result">
                  <div class="result-label">回执</div>
                  <div class="result-body">{{ ev.result_json }}</div>
                </div>

                <div v-if="ev.metadata_json && ev.event_type === 'player_dialogue'" class="event-result">
                  <details>
                    <summary>查看完整对话</summary>
                    <pre class="meta-pre">{{ ev.metadata_json }}</pre>
                  </details>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 最近一次迭代结果 -->
      <div v-if="lastIterateResult" class="card iterate-result">
        <div class="h3">最近一次迭代</div>
        <div class="muted" style="margin-top: 4px">
          Day {{ lastIterateResult.day_number - 1 }} → Day {{ lastIterateResult.day_number }}
          · 新增 {{ lastIterateResult.events_created }} 个事件
        </div>
        <div v-if="lastIterateResult.event_summary" class="section-body" style="margin-top: 10px">
          {{ lastIterateResult.event_summary }}
        </div>
        <details v-if="lastIterateResult.schedule_json" style="margin-top: 10px">
          <summary>查看次日日程 (schedule_json)</summary>
          <pre class="meta-pre">{{ lastIterateResult.schedule_json }}</pre>
        </details>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useCharacters } from '@/composables/useCharacters'
import { useEvents } from '@/composables/useEvents'
import {
  timePeriodVar, statusVar, timePeriodLabel, eventTypeLabel,
} from '@/utils'

const { activeId, active } = useCharacters()
const {
  events,
  eventsByDay,
  pendingCount,
  loading,
  error,
  operating,
  lastIterateResult,
  refresh,
  advance,
  iterate,
  auto,
} = useEvents(() => activeId.value)

const activeName = computed(() => active.value?.name ?? '')
const activeDay = computed(() => active.value?.day_number ?? 1)

function statusLabel(s: string): string {
  switch (s) {
    case 'pending': return '待推进'
    case 'active': return '进行中'
    case 'completed': return '已完成'
    default: return s
  }
}

async function onAdvance() {
  const r = await advance()
  if (r) {
    // 成功
  } else if (error.value) {
    window.alert('推进失败：' + error.value)
  }
}

async function onIterate() {
  if (!window.confirm('确定要迭代到下一天吗？这会基于今日事件生成次日的日程。')) return
  const r = await iterate()
  if (r) {
    window.alert(
      `已迭代到 Day ${r.day_number}，新增 ${r.events_created} 个事件。\n\n` +
      `事件摘要：${r.event_summary ?? '(无)'}`
    )
  } else if (error.value) {
    window.alert('迭代失败：' + error.value)
  }
}

async function onAuto() {
  if (!window.confirm('一键推演将自动推进所有 pending 事件，然后迭代到下一天。继续？')) return
  const r = await auto()
  if (r) {
    const completedCount = r.completed_events?.length ?? 0
    const iterDay = r.iterate_result?.day_number
    window.alert(
      `一键推演完成！\n` +
      `推进事件：${completedCount} 个\n` +
      `当前天数：Day ${iterDay ?? '-'}`
    )
  } else if (error.value) {
    window.alert('推演失败：' + error.value)
  }
}
</script>

<style scoped>
.events-view {
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

.timeline {
  display: flex;
  flex-direction: column;
  gap: 22px;
}
.day-block {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 18px;
  background: var(--bg-soft);
  border-bottom: 1px solid var(--border);
}
.day-num {
  font-weight: 700;
  font-size: 15px;
  color: var(--text);
}
.day-stats {
  display: flex;
  gap: 6px;
}
.event-list {
  display: flex;
  flex-direction: column;
}
.event-card {
  position: relative;
  display: flex;
  border-bottom: 1px solid var(--border);
}
.event-card:last-child { border-bottom: none; }
.event-rail {
  width: 4px;
  flex-shrink: 0;
}
.event-card.status-completed { opacity: 0.85; }
.event-card.status-completed .event-content { text-decoration: line-through; text-decoration-color: var(--text-tertiary); }

.event-main {
  flex: 1;
  padding: 12px 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.event-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.event-content {
  font-size: 14px;
  color: var(--text);
  line-height: 1.6;
}
.event-result {
  margin-top: 6px;
  padding: 8px 10px;
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
  font-size: 12.5px;
}
.result-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 4px;
}
.result-body {
  color: var(--text-secondary);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
.meta-pre {
  margin-top: 6px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow-y: auto;
}

.iterate-result {
  margin-top: 24px;
}
.section-body {
  font-size: 13.5px;
  color: var(--text);
  line-height: 1.6;
  background: var(--bg-soft);
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
}
</style>
