<template>
  <div class="settings-view">
    <header class="page-header">
      <div>
        <div class="h1">⚙ LLM 设置</div>
        <div class="muted">
          当前激活：<span class="chip chip-accent">{{ settings?.active_provider_name ?? '加载中' }}</span>
          · 模型 <code>{{ settings?.config.model ?? '-' }}</code>
          · 端点 <code>{{ settings?.config.base_url ?? '-' }}</code>
        </div>
      </div>
      <div class="row" style="gap: 8px">
        <button class="btn btn-ghost" @click="loadAll" :disabled="loading">↻ 刷新</button>
        <button class="btn btn-primary" @click="onSave" :disabled="saving">
          {{ saving ? '保存中…' : '💾 保存' }}
        </button>
      </div>
    </header>

    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div v-if="loading" class="tiny" style="padding: 20px; text-align: center">加载中…</div>

    <div v-else-if="settings" class="grid">
      <!-- 左列：选择 provider -->
      <section class="card">
        <div class="card-title">🔌 模型提供商</div>
        <div v-if="providers.length === 0" class="muted">无可用 provider</div>
        <div v-else class="provider-list">
          <button
            v-for="p in providers"
            :key="p.id"
            class="provider-item"
            :class="{ active: form.active_provider === p.id }"
            @click="selectProvider(p.id)"
          >
            <div class="row" style="justify-content: space-between">
              <span style="font-weight: 600">{{ p.name }}</span>
              <span v-if="form.active_provider === p.id" class="chip chip-accent">已激活</span>
            </div>
            <div class="tiny">
              <code>{{ p.id }}</code>
              <span v-if="p.needs_key === 'true'"> · 需要 API Key</span>
            </div>
          </button>
        </div>

        <div class="card-title" style="margin-top: 18px">🎛 默认参数</div>
        <div class="param-row">
          <label>Temperature</label>
          <input
            type="number"
            class="input"
            v-model.number="form.default_temperature"
            min="0" max="2" step="0.1"
          />
          <span class="tiny">0.0 - 2.0</span>
        </div>
        <div class="param-row">
          <label>Max Tokens</label>
          <input
            type="number"
            class="input"
            v-model.number="form.default_max_tokens"
            min="1" max="32000"
          />
        </div>
      </section>

      <!-- 右列：当前 provider 配置 + 测试 -->
      <section class="card">
        <div class="card-title">📝 当前 Provider 配置</div>
        <div class="form-row">
          <label>API Key</label>
          <div class="row" style="gap: 6px">
            <input
              :type="showKey ? 'text' : 'password'"
              class="input"
              v-model="form.active_config.api_key"
              placeholder="sk-..."
            />
            <button class="btn btn-ghost btn-sm" @click="showKey = !showKey">
              {{ showKey ? '隐藏' : '显示' }}
            </button>
          </div>
        </div>
        <div class="form-row">
          <label>Base URL</label>
          <input class="input" v-model="form.active_config.base_url" />
        </div>
        <div class="form-row">
          <label>Model</label>
          <div class="row" style="gap: 6px">
            <input class="input" v-model="form.active_config.model" />
            <button class="btn btn-ghost btn-sm" @click="onListModels" :disabled="loadingModels">
              {{ loadingModels ? '加载中…' : '🔍 拉取模型' }}
            </button>
          </div>
          <div v-if="models.length" class="model-list">
            <button
              v-for="m in models"
              :key="m.id"
              class="model-item"
              :class="{ active: form.active_config.model === m.id }"
              @click="form.active_config.model = m.id"
            >
              {{ m.id }}
            </button>
          </div>
        </div>

        <div class="card-title" style="margin-top: 18px">🧪 连接测试</div>
        <div class="form-row">
          <label>测试 Prompt</label>
          <textarea
            class="textarea"
            v-model="form.test_prompt"
            rows="2"
            placeholder="你好，请用一句话自我介绍。"
          ></textarea>
        </div>
        <div class="row" style="gap: 8px; margin-top: 8px">
          <button class="btn btn-ghost" @click="onTest" :disabled="testing">
            {{ testing ? '测试中…' : '⚡ 测试连接' }}
          </button>
          <button class="btn btn-ghost" @click="onLatency" :disabled="testingLatency">
            {{ testingLatency ? '测试中…' : '⏱ 延迟测试' }}
          </button>
        </div>
        <div v-if="testResult" class="test-result" :class="testResult.success ? 'ok' : 'err'">
          <div class="row" style="gap: 8px">
            <span class="chip" :class="testResult.success ? 'chip-success' : 'chip-danger'">
              {{ testResult.success ? '✓ 成功' : '✗ 失败' }}
            </span>
            <span class="tiny">{{ testResult.latency_ms }}ms</span>
            <span class="tiny">模型：{{ testResult.model }}</span>
          </div>
          <div v-if="testResult.response_text" class="response-text">
            {{ testResult.response_text }}
          </div>
          <div v-else-if="!testResult.success" class="response-text">
            {{ testResult.message }}
          </div>
        </div>
        <div v-if="latencyResult" class="test-result ok">
          <div class="row" style="gap: 8px">
            <span class="chip chip-success">⏱ 延迟</span>
            <span class="tiny">TTFT: {{ latencyResult.ttft_ms }}ms</span>
            <span class="tiny">Total: {{ latencyResult.total_ms }}ms</span>
            <span class="tiny">Chunks: {{ latencyResult.chunks }}</span>
          </div>
          <div v-if="latencyResult.content" class="response-text">
            {{ latencyResult.content }}
          </div>
        </div>
      </section>
    </div>

    <details v-if="settings" class="raw-details">
      <summary>查看原始设置（调试）</summary>
      <div class="tiny" style="margin-bottom: 4px">{{ settings.settings_file_path }}</div>
      <pre class="meta-pre">{{ JSON.stringify(settings, null, 2) }}</pre>
    </details>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { llmSettings as llmApi, testTools, ApiError } from '@/api'
import type { LLMSettingsResponse, ModelItem, LLMTestResponse, LatencyTestResponse } from '@/types'

const settings = ref<LLMSettingsResponse | null>(null)
const providers = ref<{ id: string; name: string; needs_key: string }[]>([])
const models = ref<ModelItem[]>([])

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const testingLatency = ref(false)
const loadingModels = ref(false)
const showKey = ref(false)
const error = ref<string | null>(null)

const testResult = ref<LLMTestResponse | null>(null)
const latencyResult = ref<LatencyTestResponse | null>(null)

const form = reactive({
  active_provider: 'deepseek',
  active_config: { api_key: '', base_url: '', model: '' },
  default_temperature: 0.7,
  default_max_tokens: 1000,
  test_prompt: '你好，请用一句话自我介绍。',
})

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const [s, p] = await Promise.all([llmApi.get(), llmApi.providers()])
    settings.value = s
    providers.value = p.providers
    syncForm(s)
  } catch (e) {
    error.value = e instanceof ApiError ? e.detail : (e as Error).message
  } finally {
    loading.value = false
  }
}

function syncForm(s: LLMSettingsResponse) {
  form.active_provider = s.active_provider
  form.active_config = { ...s.config }
  form.default_temperature = s.default_temperature
  form.default_max_tokens = s.default_max_tokens
  testResult.value = null
  latencyResult.value = null
}

function selectProvider(id: string) {
  if (!settings.value) return
  form.active_provider = id
  // 修复：之前直接用 settings.providers[id]，如果是 masked 空对象（未配置过的 provider），
  //       base_url/model 会是空串，保存时会覆盖掉默认值导致 LLMService reload 失败。
  // 解决：base_url/model 永远从 PROVIDER_DEFAULTS 或已有值兜底。
  const stored = settings.value.providers[id] || { api_key: '', base_url: '', model: '' }
  const baseUrl = (stored.base_url && stored.base_url.trim()) || defaultBaseUrl(id)
  const model = (stored.model && stored.model.trim()) || defaultModel(id)
  form.active_config = {
    api_key: stored.api_key ?? '',
    base_url: baseUrl,
    model: model,
  }
  models.value = []
  testResult.value = null
  latencyResult.value = null
}

// 前端兜底：与后端 PROVIDER_DEFAULTS 保持一致（防止后端默认值更新后这里不一致）
// 注意：base_url/model 兜底在后端已实现；这里只是"切到空 provider 时表单显示默认值"
const _PROVIDER_DEFAULTS: Record<string, { base_url: string; model: string }> = {
  deepseek: { base_url: 'https://api.deepseek.com', model: 'deepseek-chat' },
  qwen:     { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-turbo' },
  zhipu:    { base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  ollama:   { base_url: 'http://localhost:11434/v1', model: 'qwen2.5:7b' },
  openai:   { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  agnes:    { base_url: 'https://apihub.agnes-ai.com/v1', model: 'agnes-1.5-flash' },
}
function defaultBaseUrl(id: string): string {
  return _PROVIDER_DEFAULTS[id]?.base_url ?? ''
}
function defaultModel(id: string): string {
  return _PROVIDER_DEFAULTS[id]?.model ?? ''
}

async function onSave() {
  if (saving.value) return
  saving.value = true
  error.value = null
  try {
    // [P0#2] 防止 masked 串回写覆盖真 key
    // 后端 mask_api_key 返回形如 sk-12****5678；用户没主动改就直接保存 → 会把真 key 覆盖成 masked
    const isMasked = (s: unknown): boolean => typeof s === 'string' && /\*{2,}/.test(s)
    const config = { ...form.active_config }
    if (config.api_key && isMasked(config.api_key)) {
      // masked 串 → 跳过该字段，后端会保留 store 里的真 key
      delete config.api_key
    } else if (typeof config.api_key === 'string') {
      config.api_key = config.api_key.trim()
    }

    const updated = await llmApi.update({
      active_provider: form.active_provider,
      active_config: config,
      default_temperature: form.default_temperature,
      default_max_tokens: form.default_max_tokens,
    })
    settings.value = updated
    syncForm(updated)
    window.alert('已保存')
  } catch (e) {
    error.value = e instanceof ApiError ? e.detail : (e as Error).message
  } finally {
    saving.value = false
  }
}

async function onTest() {
  if (testing.value) return
  testing.value = true
  error.value = null
  testResult.value = null
  try {
    const r = await llmApi.test({
      provider_id: form.active_provider,
      api_key: form.active_config.api_key,
      base_url: form.active_config.base_url,
      model: form.active_config.model,
      test_prompt: form.test_prompt,
    })
    testResult.value = r
  } catch (e) {
    error.value = e instanceof ApiError ? e.detail : (e as Error).message
  } finally {
    testing.value = false
  }
}

async function onLatency() {
  if (testingLatency.value) return
  testingLatency.value = true
  error.value = null
  latencyResult.value = null
  try {
    const r = await testTools.latency({
      provider_id: form.active_provider,
      api_key: form.active_config.api_key,
      base_url: form.active_config.base_url,
      model: form.active_config.model,
    })
    latencyResult.value = r
  } catch (e) {
    error.value = e instanceof ApiError ? e.detail : (e as Error).message
  } finally {
    testingLatency.value = false
  }
}

async function onListModels() {
  if (loadingModels.value) return
  loadingModels.value = true
  models.value = []
  try {
    const r = await testTools.listModels({
      provider_id: form.active_provider,
      base_url: form.active_config.base_url,
      api_key: form.active_config.api_key,
    })
    models.value = r.models
  } catch (e) {
    error.value = e instanceof ApiError ? e.detail : (e as Error).message
  } finally {
    loadingModels.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.settings-view {
  padding: 22px 28px;
  overflow-y: auto;
  flex: 1;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}
code {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--bg-soft);
  padding: 1px 5px;
  border-radius: 3px;
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
.card-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text);
  margin-bottom: 14px;
}

.provider-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.provider-item {
  text-align: left;
  background: var(--bg-soft);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: background 0.12s, border-color 0.12s;
}
.provider-item:hover { background: var(--bg-hover); }
.provider-item.active {
  background: var(--accent-soft);
  border-color: var(--accent);
}

.param-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.param-row label {
  font-size: 12.5px;
  color: var(--text-secondary);
  min-width: 100px;
}
.param-row .input { flex: 1; }

.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}
.form-row label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.model-list {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-height: 160px;
  overflow-y: auto;
  padding: 6px;
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
}
.model-item {
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 999px;
  color: var(--text-secondary);
  background: var(--bg-card);
  border: 1px solid var(--border);
}
.model-item:hover { border-color: var(--accent); }
.model-item.active {
  background: var(--accent);
  color: var(--accent-text);
  border-color: var(--accent);
}

.test-result {
  margin-top: 12px;
  padding: 12px 14px;
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-left: 3px solid var(--accent);
}
.test-result.ok { border-left-color: var(--success); }
.test-result.err { border-left-color: var(--danger); }
.response-text {
  font-size: 13px;
  color: var(--text);
  line-height: 1.6;
  white-space: pre-wrap;
}

.raw-details {
  margin-top: 24px;
}
.raw-details summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--text-tertiary);
  user-select: none;
}
.meta-pre {
  font-family: var(--font-mono);
  font-size: 11.5px;
  background: var(--bg-soft);
  padding: 12px;
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 400px;
  overflow-y: auto;
}
</style>
