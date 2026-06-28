<template>
  <div class="chat-view">
    <div v-if="!activeId" class="empty-state">
      <div style="font-size: 64px">💬</div>
      <div class="h2">请先选择或创建角色</div>
      <div class="muted">在左侧选择一个角色，或前往「角色创建」</div>
      <router-link class="btn btn-primary" to="/create" style="margin-top: 12px">
        去创建 →
      </router-link>
    </div>

    <template v-else>
      <!-- 左侧：会话列表 -->
      <aside class="session-panel">
        <div class="session-header">
          <div class="h3">会话</div>
          <button class="btn btn-primary btn-sm" @click="onNewSession">+ 新对话</button>
        </div>

        <div v-if="loadingSessions" class="tiny" style="padding: 12px">加载中…</div>
        <div v-else-if="sessions.length === 0" class="empty" style="padding: 20px 12px">
          <div>暂无会话</div>
          <div class="tiny">点击「+ 新对话」开始</div>
        </div>

        <div class="session-list">
          <div
            v-for="s in sessions"
            :key="s.id"
            class="session-item"
            :class="{ active: activeSessionId === s.id }"
            @click="onSelect(s.id)"
          >
            <div class="session-title">{{ s.title || '新对话' }}</div>
            <div class="session-meta">
              <span class="tiny">{{ s.message_count }} 条</span>
              <span class="tiny">{{ formatRelative(s.updated_at) }}</span>
            </div>
            <div class="session-actions">
              <button class="icon-btn" @click.stop="onRename(s.id, s.title)" title="重命名">✎</button>
              <button class="icon-btn danger" @click.stop="onDelete(s.id)" title="删除">×</button>
            </div>
          </div>
        </div>
      </aside>

      <!-- 右侧：聊天区 -->
      <section class="chat-panel">
        <header class="chat-header">
          <div>
            <div class="h2">{{ activeName }}</div>
            <div class="muted">
              <span v-if="sessionTitle">「{{ sessionTitle }}」</span>
              <span v-else>开始一段新对话</span>
            </div>
          </div>
          <div class="row">
            <button class="btn btn-ghost btn-sm" @click="onTriggerGrowth" :disabled="triggering">
              {{ triggering ? '成长中…' : '🌱 触发成长' }}
            </button>
            <button class="btn btn-ghost btn-sm" @click="onClearSession">清空对话</button>
          </div>
        </header>

        <div ref="messagesEl" class="messages">
          <div v-if="messages.length === 0" class="empty" style="margin-top: 80px">
            <div style="font-size: 48px">✨</div>
            <div>说一句问候开始吧</div>
          </div>

          <div v-for="m in messages" :key="m.id" class="message-row" :class="m.role">
            <div class="avatar">{{ m.role === 'user' ? '我' : 'AI' }}</div>
            <div class="bubble-wrap">
              <div class="bubble">
                <div v-if="m.pending && !m.content" class="pending">
                  <div class="thinking-text" v-if="m.thinking_message">{{ m.thinking_message }}</div>
                  <div class="thinking-dots">
                    <span class="dot1"></span><span class="dot2"></span><span class="dot3"></span>
                  </div>
                </div>
                <div v-else class="bubble-content">{{ m.content }}</div>

                <div v-if="m.role === 'assistant' && !m.pending && (m.emotion || m.action || m.expression)" class="meta-row">
                  <span v-if="m.emotion" class="chip chip-accent">{{ m.emotion }}</span>
                  <span v-if="m.action" class="chip">🎬 {{ m.action }}</span>
                  <span v-if="m.expression" class="chip">😊 {{ m.expression }}</span>
                </div>

                <details v-if="m.role === 'assistant' && !m.pending && (m.director_raw || m.actor_raw)" class="llm-details">
                  <summary>查看 LLM 管线原始响应</summary>
                  <div v-if="m.director_raw" class="llm-block">
                    <div class="llm-label">Director</div>
                    <pre class="llm-pre">{{ m.director_raw }}</pre>
                  </div>
                  <div v-if="m.actor_raw" class="llm-block">
                    <div class="llm-label">Actor</div>
                    <pre class="llm-pre">{{ m.actor_raw }}</pre>
                  </div>
                </details>
              </div>
              <div class="timestamp">{{ formatDate(m.timestamp) }}</div>
            </div>
          </div>

          <div v-if="error" class="alert alert-error" style="margin-top: 12px">
            {{ error }}
          </div>
        </div>

        <div class="input-area">
          <textarea
            v-model="inputText"
            class="textarea chat-input"
            rows="1"
            placeholder="输入消息，回车发送，Shift+回车换行"
            @keydown.enter.exact.prevent="onSend"
            :disabled="sending"
          ></textarea>
          <button
            class="btn btn-primary send-btn"
            @click="onSend"
            :disabled="!canSend"
          >
            <span v-if="sending" class="spinner"></span>
            {{ sending ? '生成中…' : '发送' }}
          </button>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useCharacters } from '@/composables/useCharacters'
import { useSessions } from '@/composables/useSessions'
import { useChat } from '@/composables/useChat'
import { growth as growthApi } from '@/api'
import { formatDate, formatRelative } from '@/utils'

const { activeId, active } = useCharacters()

const {
  sessions,
  activeSessionId,
  loading: loadingSessions,
  createNew,
  select: selectSession,
  rename,
  remove,
} = useSessions(() => activeId.value)

const {
  messages,
  sessionTitle,
  sending,
  error,
  loadSession,
  send,
  clear,
} = useChat()

// 同步 session 选择
watch(activeSessionId, (id) => {
  if (id != null) loadSession(id)
}, { immediate: true })

const inputText = ref('')
const messagesEl = ref<HTMLElement | null>(null)
const triggering = ref(false)
const activeName = computed(() => active.value?.name ?? '')
const canSend = computed(() => inputText.value.trim().length > 0 && !sending.value)

// 滚动到底部
async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  }
}
watch(messages, scrollToBottom, { deep: true })

async function onSend() {
  if (!activeId.value || !canSend.value) return
  const text = inputText.value
  inputText.value = ''
  await send(activeId.value, text)
  await scrollToBottom()
}

async function onNewSession() {
  await createNew()
}

async function onSelect(id: number) {
  await selectSession(id)
  await scrollToBottom()
}

async function onRename(id: number, oldTitle: string) {
  const newTitle = window.prompt('新的会话标题', oldTitle)
  if (newTitle && newTitle.trim() && newTitle !== oldTitle) {
    await rename(id, newTitle.trim())
  }
}

async function onDelete(id: number) {
  if (window.confirm('确定删除该会话及其全部消息？')) {
    await remove(id)
  }
}

function onClearSession() {
  clear()
}

async function onTriggerGrowth() {
  if (!activeId.value || triggering.value) return
  triggering.value = true
  try {
    const r = await growthApi.trigger(activeId.value)
    window.alert(
      `成长完成！\n\n事件摘要：${r.event_summary ?? '(无)'}\n\n` +
      `新增记忆：${r.new_memories ?? '(无)'}\n\n` +
      `查看详情请前往「状态面板」`
    )
  } catch (e) {
    window.alert('触发失败：' + (e as Error).message)
  } finally {
    triggering.value = false
  }
}
</script>

<style scoped>
.chat-view {
  flex: 1;
  display: flex;
  min-height: 0;
}

.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 40px;
}

.session-panel {
  width: 240px;
  min-width: 240px;
  background: var(--bg-card);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}
.session-header {
  padding: 14px 14px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
}
.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.session-item {
  position: relative;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.12s;
}
.session-item:hover { background: var(--bg-soft); }
.session-item.active {
  background: var(--accent-soft);
}
.session-title {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-item.active .session-title { color: var(--accent); font-weight: 600; }
.session-meta {
  display: flex;
  justify-content: space-between;
  margin-top: 3px;
}
.session-actions {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  display: none;
  gap: 2px;
}
.session-item:hover .session-actions { display: flex; }
.icon-btn {
  width: 22px;
  height: 22px;
  border-radius: 4px;
  font-size: 14px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
}
.icon-btn:hover { background: var(--bg-card); color: var(--text); }
.icon-btn.danger:hover { color: var(--danger); }

.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-card);
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.message-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.message-row.user {
  flex-direction: row-reverse;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--bg-soft);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border: 1px solid var(--border);
}
.message-row.user .avatar {
  background: var(--accent);
  color: var(--accent-text);
  border-color: var(--accent);
}
.message-row.assistant .avatar {
  background: var(--bg-soft);
  color: var(--text);
  border: 1px solid var(--border);
}

.bubble-wrap {
  max-width: 70%;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.message-row.user .bubble-wrap { align-items: flex-end; }
.bubble {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.65;
  color: var(--text);
  word-break: break-word;
  white-space: pre-wrap;
  box-shadow: var(--shadow-sm);
}
.message-row.user .bubble {
  background: var(--accent);
  color: var(--accent-text);
  border-color: var(--accent);
}
.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}
.message-row.user .meta-row { display: none; }
.timestamp {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 0 4px;
}

.pending {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0;
  min-height: 24px;
}
.thinking-text {
  font-size: 13px;
  color: var(--text-tertiary);
  font-style: italic;
}
.thinking-dots {
  display: flex;
  gap: 4px;
}
.thinking-dots > span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  animation: bounce 1.2s infinite ease-in-out;
}
.thinking-dots > span:nth-child(2) { animation-delay: 0.15s; }
.thinking-dots > span:nth-child(3) { animation-delay: 0.3s; }
@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.5; }
  40% { transform: translateY(-4px); opacity: 1; }
}

.llm-details {
  margin-top: 8px;
  font-size: 12px;
}
.llm-details summary {
  cursor: pointer;
  color: var(--text-tertiary);
  user-select: none;
}
.llm-block { margin-top: 6px; }
.llm-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 4px;
}
.llm-pre {
  background: var(--bg-soft);
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  max-height: 180px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.input-area {
  border-top: 1px solid var(--border);
  padding: 12px 20px;
  background: var(--bg-card);
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.chat-input {
  flex: 1;
  min-height: 44px;
  max-height: 200px;
  resize: none;
}
.send-btn {
  height: 44px;
  min-width: 88px;
}
.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--accent-glow);
  border-top-color: var(--accent-text);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
