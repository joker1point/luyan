<template>
  <div class="create-view">
    <header class="page-header">
      <div>
        <div class="h1">🌱 创建角色</div>
        <div class="muted">一句话描述或上传 TXT 故事 → 自动生成名称、世界设定、6 维人格、初始记忆</div>
      </div>
    </header>

    <div class="create-grid">
      <section class="card form-card">
        <div class="tabs">
          <button class="tab" :class="{ active: mode === 'text' }" @click="mode = 'text'">
            文本输入
          </button>
          <button class="tab" :class="{ active: mode === 'file' }" @click="mode = 'file'">
            TXT 文件
          </button>
        </div>

        <div v-if="mode === 'text'" class="form-block">
          <label class="label">角色描述</label>
          <textarea
            v-model="description"
            class="textarea"
            rows="10"
            placeholder="例如：一个精灵弓箭手艾琳，住在中土魔法森林，性格温和而坚定，喜欢在清晨练习射箭……"
          ></textarea>
          <div class="row" style="justify-content: space-between">
            <span class="tiny">{{ description.length }} 字</span>
            <button class="btn btn-ghost btn-sm" @click="description = ''">清空</button>
          </div>
        </div>

        <div v-else class="form-block">
          <label class="label">故事文件 (.txt)</label>
          <input
            type="file"
            accept=".txt"
            @change="onFileChange"
            class="file-input"
          />
          <div v-if="storyFile" class="row file-info">
            <span class="chip chip-info">📄 {{ storyFile.name }}</span>
            <span class="tiny">{{ formatSize(storyFile.size) }}</span>
            <button class="btn btn-ghost btn-sm" @click="storyFile = null">移除</button>
          </div>
          <label class="label" style="margin-top: 14px">补充描述（可选）</label>
          <textarea
            v-model="description"
            class="textarea"
            rows="3"
            placeholder="附加对角色的期望，将与文件内容合并"
          ></textarea>
        </div>

        <div v-if="error" class="alert alert-error" style="margin-top: 12px">
          {{ error }}
        </div>

        <div class="row" style="margin-top: 18px; justify-content: flex-end">
          <button
            class="btn btn-primary"
            :disabled="!canSubmit || submitting"
            @click="submit"
          >
            <span v-if="submitting" class="spinner"></span>
            {{ submitting ? '生成中（可能需要 10-30 秒）…' : '生成角色' }}
          </button>
        </div>
      </section>

      <section class="card result-card" v-if="result">
        <div class="row" style="justify-content: space-between; align-items: flex-start">
          <div>
            <div class="h2">✨ {{ result.name }}</div>
            <div v-if="result.description" class="muted" style="margin-top: 4px">
              {{ result.description }}
            </div>
          </div>
          <div class="row">
            <span class="chip">Day {{ result.day_number }}</span>
            <span class="chip chip-accent">已激活</span>
          </div>
        </div>

        <div v-if="result.world_setting" class="section">
          <div class="section-title">🌍 世界设定</div>
          <div class="section-body">{{ result.world_setting }}</div>
        </div>

        <div v-if="personalityEntries.length" class="section">
          <div class="section-title">🎭 6 维人格</div>
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
        </div>

        <div v-if="stateEntries.length" class="section">
          <div class="section-title">📍 当前状态</div>
          <div class="state-grid">
            <div v-for="[k, v] in stateEntries" :key="k" class="state-row">
              <span class="state-key">{{ k }}</span>
              <span class="state-val">{{ v }}</span>
            </div>
          </div>
        </div>

        <div v-if="speakingStyleList.length" class="section">
          <div class="section-title">🗣️ 说话风格</div>
          <div class="row" style="flex-wrap: wrap; gap: 6px">
            <span v-for="s in speakingStyleList" :key="s" class="chip chip-info">{{ s }}</span>
          </div>
        </div>

        <div v-if="valuesList.length" class="section">
          <div class="section-title">💎 核心信念</div>
          <div class="row" style="flex-wrap: wrap; gap: 6px">
            <span v-for="s in valuesList" :key="s" class="chip chip-accent">{{ s }}</span>
          </div>
        </div>

        <div v-if="habitsList.length" class="section">
          <div class="section-title">🌿 日常习惯</div>
          <div class="row" style="flex-wrap: wrap; gap: 6px">
            <span v-for="s in habitsList" :key="s" class="chip chip-success">{{ s }}</span>
          </div>
        </div>

        <div v-if="result.long_term_goal" class="section">
          <div class="section-title">🎯 长期目标</div>
          <div class="section-body">{{ result.long_term_goal }}</div>
        </div>

        <details v-if="result.creation_raw" class="raw-details">
          <summary>查看 Creation LLM 原始响应（调试）</summary>
          <pre class="raw-pre">{{ result.creation_raw }}</pre>
        </details>

        <div class="row" style="margin-top: 18px; justify-content: flex-end; gap: 8px">
          <button class="btn btn-ghost" @click="result = null">再创建一个</button>
          <router-link class="btn btn-primary" to="/chat">去对话 →</router-link>
        </div>
      </section>

      <section v-else class="card empty-card">
        <div class="empty">
          <div style="font-size: 48px">🌱</div>
          <div class="h3">在这里查看生成结果</div>
          <div class="tiny">填入描述或上传文件，点击「生成角色」</div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { characters as charactersApi, ApiError } from '@/api'
import { useCharacters } from '@/composables/useCharacters'
import { safeJsonParse } from '@/utils'
import type { ToastShowFn } from '@/composables/useToast'
import type { Character, PersonalityMap, CurrentState } from '@/types'

const { refresh: refreshList, setActive } = useCharacters()

const showToast = inject<ToastShowFn>('showToast')
const router = useRouter()

const mode = ref<'text' | 'file'>('text')
const description = ref('')
const storyFile = ref<File | null>(null)
const submitting = ref(false)
const error = ref<string | null>(null)
const result = ref<Character | null>(null)

const canSubmit = computed(() => {
  if (mode.value === 'text') return description.value.trim().length > 0
  return !!storyFile.value
})

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    storyFile.value = input.files[0] ?? null
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(2) + ' MB'
}

async function submit() {
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  error.value = null
  try {
    let created: Character
    if (mode.value === 'text') {
      created = await charactersApi.createByText(description.value.trim())
    } else {
      created = await charactersApi.createByFile(storyFile.value!, description.value.trim() || undefined)
    }
    result.value = created
    setActive(created.id)
    await refreshList()
    description.value = ''
    storyFile.value = null

    // 成功反馈
    showToast?.(`角色「${created.name}」创建成功！`, 'success', 2500)

    // 1.5s 后自动跳转到对话页
    setTimeout(() => {
      router.push('/chat')
    }, 1500)
  } catch (e) {
    const msg = e instanceof ApiError ? e.detail : (e as Error).message
    error.value = msg
    showToast?.(`创建失败：${msg}`, 'error', 4000)
  } finally {
    submitting.value = false
  }
}

// 解析后的字段
const personalityParsed = computed<PersonalityMap>(() =>
  safeJsonParse<PersonalityMap>(result.value?.personality, {})
)
const currentStateParsed = computed<CurrentState>(() =>
  safeJsonParse<CurrentState>(result.value?.current_state, {})
)
const speakingStyleList = computed<string[]>(() =>
  safeJsonParse<string[]>(result.value?.speaking_style, [])
)
const valuesList = computed<string[]>(() => safeJsonParse<string[]>(result.value?.values, []))
const habitsList = computed<string[]>(() => safeJsonParse<string[]>(result.value?.habits, []))

const personalityEntries = computed(() =>
  Object.entries(personalityParsed.value).sort(([, a], [, b]) => Number(b) - Number(a))
)
const stateEntries = computed(() => Object.entries(currentStateParsed.value))
</script>

<style scoped>
.create-view {
  padding: 24px 28px;
  overflow-y: auto;
  flex: 1;
}
.page-header {
  margin-bottom: 20px;
}
.create-grid {
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 18px;
  align-items: start;
}
@media (max-width: 980px) {
  .create-grid { grid-template-columns: 1fr; }
}

.tabs {
  display: flex;
  gap: 4px;
  background: var(--bg-soft);
  padding: 4px;
  border-radius: var(--radius);
  margin-bottom: 18px;
}
.tab {
  flex: 1;
  padding: 7px 0;
  border-radius: 7px;
  font-size: 13px;
  color: var(--text-secondary);
  background: transparent;
  transition: background 0.15s, color 0.15s;
}
.tab.active {
  background: var(--bg-card);
  color: var(--text);
  box-shadow: var(--shadow-sm);
  font-weight: 600;
}

.form-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-top: 4px;
}
.file-input {
  padding: 8px 0;
  font-size: 13px;
}
.file-info {
  background: var(--bg-soft);
  padding: 6px 10px;
  border-radius: var(--radius);
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

.section { margin-top: 16px; }
.section-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
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

.personality-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 18px;
}
.personality-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
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
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
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

.raw-details {
  margin-top: 16px;
  font-size: 12px;
}
.raw-details summary {
  cursor: pointer;
  color: var(--text-tertiary);
}
.raw-pre {
  margin-top: 8px;
  padding: 12px;
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.5;
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.empty-card {
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
