<template>
  <div class="jiwen-config">
    <div v-if="!characterId" class="empty">请先选择角色</div>
    <div v-else-if="loading" class="loading">加载中…</div>
    <div v-else-if="!params" class="empty">加载失败</div>
    <template v-else>
      <nav class="config-tabs">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="{ active: activeTab === tab.key }"
          class="tab-btn"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </nav>

      <!-- 情绪参数 -->
      <div v-if="activeTab === 'jiwen'" class="config-section">
        <h4>漂移率（Drift Rates）</h4>
        <p class="muted">控制情绪数值的自然变化速度（每 tick）</p>
        <div
          v-for="item in rateItems"
          :key="item.key"
          class="param-row"
        >
          <label class="param-label">
            <span>{{ item.label }}</span>
            <span class="param-key">{{ item.key }}</span>
          </label>
          <input
            type="range"
            v-model.number="params.jiwen.rates[item.key]"
            :min="item.min"
            :max="item.max"
            :step="item.step"
            class="param-slider"
          />
          <span class="param-val">{{ params.jiwen.rates[item.key].toFixed(4) }}</span>
        </div>

        <h4 style="margin-top: 18px">触发阈值（Thresholds）</h4>
        <p class="muted">控制什么时候角色会触发主动行为</p>
        <div
          v-for="item in thresholdItems"
          :key="item.key"
          class="param-row"
        >
          <label class="param-label">
            <span>{{ item.label }}</span>
            <span class="param-key">{{ item.key }}</span>
          </label>
          <input
            type="range"
            v-model.number="params.jiwen.thresholds[item.key]"
            min="0"
            max="1"
            step="0.01"
            class="param-slider"
          />
          <span class="param-val">{{ params.jiwen.thresholds[item.key].toFixed(2) }}</span>
        </div>

        <h4 style="margin-top: 18px">活动类型（Activities）</h4>
        <p class="muted">不同活动对沉浸度的影响</p>
        <div
          v-for="(val, key) in params.jiwen.activities"
          :key="key"
          class="param-row"
        >
          <label class="param-label">
            <span>{{ activityLabels[key] || key }}</span>
            <span class="param-key">{{ key }}</span>
          </label>
          <input
            type="range"
            v-model.number="params.jiwen.activities[key]"
            min="0"
            max="1"
            step="0.05"
            class="param-slider"
          />
          <span class="param-val">{{ val.toFixed(2) }}</span>
        </div>

        <h4 style="margin-top: 18px">主动消息 Fallback 模板</h4>
        <p class="muted">LLM 不可用时使用这些自定义模板（留空则用默认 6 条）</p>
        <div v-for="(_, idx) in params.jiwen.fallback_templates" :key="idx" class="fallback-row">
          <input
            v-model="params.jiwen.fallback_templates[idx]"
            class="input"
            placeholder="主动消息内容"
          />
          <button class="btn btn-ghost btn-sm" @click="removeFallback(idx)">×</button>
        </div>
        <button class="btn btn-ghost btn-sm" @click="addFallback">+ 添加模板</button>
      </div>

      <!-- 记忆衰减 -->
      <div v-if="activeTab === 'decay'" class="config-section">
        <h4>主题衰减率</h4>
        <p class="muted">不同主题的记忆衰减速度（每主题独立）</p>
        <div
          v-for="(theme, key) in params.decay.themes"
          :key="key"
          class="theme-row"
        >
          <div class="theme-title">{{ themeLabels[key] || key }}</div>
          <div class="param-row">
            <label class="param-label">
              <span>基础衰减率</span>
            </label>
            <input
              type="range"
              v-model.number="params.decay.themes[key].base_decay_rate"
              min="0"
              max="0.2"
              step="0.001"
              class="param-slider"
            />
            <span class="param-val">{{ params.decay.themes[key].base_decay_rate.toFixed(3) }}</span>
          </div>
          <div class="param-row">
            <label class="param-label">
              <span>最小半衰期 (天)</span>
            </label>
            <input
              type="number"
              v-model.number="params.decay.themes[key].min_half_life_days"
              min="0.1"
              max="365"
              step="0.1"
              class="input"
            />
            <span class="param-val">天</span>
          </div>
          <div class="param-row">
            <label class="param-label">
              <span>最大半衰期 (天)</span>
            </label>
            <input
              type="number"
              v-model.number="params.decay.themes[key].max_half_life_days"
              min="1"
              max="730"
              step="1"
              class="input"
            />
            <span class="param-val">天</span>
          </div>
        </div>

        <h4 style="margin-top: 18px">遗忘阈值</h4>
        <p class="muted">strength 低于此值 → 标记为遗忘</p>
        <div class="param-row">
          <label class="param-label">
            <span>should_forget_threshold</span>
          </label>
          <input
            type="range"
            v-model.number="params.decay.should_forget_threshold"
            min="0"
            max="5"
            step="0.1"
            class="param-slider"
          />
          <span class="param-val">{{ params.decay.should_forget_threshold.toFixed(1) }}</span>
        </div>
      </div>

      <!-- 摘要触发 -->
      <div v-if="activeTab === 'summary'" class="config-section">
        <h4>摘要触发阈值</h4>
        <div class="param-row">
          <label class="param-label">
            <span>最少消息间隔</span>
          </label>
          <input
            type="number"
            v-model.number="params.summary.min_messages_between"
            min="1"
            max="500"
            step="1"
            class="input"
          />
          <span class="param-val">条</span>
        </div>
        <div class="param-row">
          <label class="param-label">
            <span>最大消息间隔</span>
          </label>
          <input
            type="number"
            v-model.number="params.summary.max_messages_between"
            min="10"
            max="1000"
            step="1"
            class="input"
          />
          <span class="param-val">条</span>
        </div>
        <div class="param-row">
          <label class="param-label">
            <span>遗忘比例触发</span>
          </label>
          <input
            type="range"
            v-model.number="params.summary.forgotten_ratio_trigger"
            min="0"
            max="1"
            step="0.05"
            class="param-slider"
          />
          <span class="param-val">{{ params.summary.forgotten_ratio_trigger.toFixed(2) }}</span>
        </div>
        <div class="param-row">
          <label class="param-label">
            <span>时间间隔 (天)</span>
          </label>
          <input
            type="number"
            v-model.number="params.summary.time_gap_days"
            min="1"
            max="90"
            step="1"
            class="input"
          />
          <span class="param-val">天</span>
        </div>
      </div>

      <!-- Session 配置 -->
      <div v-if="activeTab === 'session'" class="config-section">
        <h4>主动消息 Session 复用窗口</h4>
        <p class="muted">多少小时内的 session 会被复用（否则新建）</p>
        <div class="param-row">
          <label class="param-label">
            <span>reuse_window_hours</span>
          </label>
          <input
            type="number"
            v-model.number="params.session.reuse_window_hours"
            min="0.5"
            max="168"
            step="0.5"
            class="input"
          />
          <span class="param-val">小时</span>
        </div>
      </div>

      <div class="config-actions">
        <button class="btn btn-primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
        <button class="btn btn-ghost" :disabled="loading" @click="load">重新加载</button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { jiwen as jiwenApi, type JiwenParamsResponse } from '@/api'
import type { ToastShowFn } from '@/composables/useToast'

const props = defineProps<{ characterId: number | null }>()

const showToast = inject<ToastShowFn>('showToast')

const tabs = [
  { key: 'jiwen', label: '🎭 情绪参数' },
  { key: 'decay', label: '🧠 记忆衰减' },
  { key: 'summary', label: '📝 摘要触发' },
  { key: 'session', label: '💬 会话设置' },
]

const activeTab = ref<'jiwen' | 'decay' | 'summary' | 'session'>('jiwen')

const loading = ref(false)
const saving = ref(false)
const params = ref<JiwenParamsResponse | null>(null)

const rateLabels: Record<string, string> = {
  pride_regression: '自尊回归',
  pride_inflation: '自尊膨胀',
  pride_external_factor: '自尊外因',
  valence_decay: '效价衰减',
  valence_external_factor: '效价外因',
  arousal_decay: '唤醒衰减',
  arousal_external_factor: '唤醒外因',
  connection_toward_target: '连接需求趋中',
  immersion_external_factor: '沉浸外因',
  immersion_decay: '沉浸衰减',
  baselinePride: '自尊基线',
  baselineValence: '效价基线',
  baselineArousal: '唤醒基线',
  baselineImmersion: '沉浸基线',
  baselineConnection: '连接基线',
}

const rateBounds: Record<string, { min: number; max: number; step: number }> = {
  pride_regression:                 { min: 0, max: 0.2, step: 0.001 },
  pride_inflation:                  { min: 0, max: 0.2, step: 0.001 },
  pride_external_factor:            { min: 0, max: 0.5, step: 0.01 },
  valence_decay:                    { min: 0, max: 0.2, step: 0.001 },
  valence_external_factor:          { min: 0, max: 0.5, step: 0.01 },
  arousal_decay:                    { min: 0, max: 0.2, step: 0.001 },
  arousal_external_factor:          { min: 0, max: 0.5, step: 0.01 },
  connection_toward_target:         { min: 0, max: 0.5, step: 0.01 },
  immersion_external_factor:        { min: 0, max: 0.5, step: 0.01 },
  immersion_decay:                  { min: 0, max: 0.2, step: 0.001 },
  baselinePride:                    { min: -1, max: 1, step: 0.05 },
  baselineValence:                  { min: -1, max: 1, step: 0.05 },
  baselineArousal:                  { min: -1, max: 1, step: 0.05 },
  baselineImmersion:                { min: 0, max: 1, step: 0.05 },
  baselineConnection:               { min: 0, max: 1, step: 0.05 },
}

const rateItems = computed(() =>
  params.value
    ? Object.entries(params.value.jiwen.rates).map(([key, value]) => ({
        key,
        label: rateLabels[key] || key,
        value,
        ...(rateBounds[key] || { min: 0, max: 1, step: 0.001 }),
      }))
    : [],
)

const thresholdLabels: Record<string, string> = {
  observation: '观察阈值',
  consider_contact: '考虑联系阈值',
  contact_urgent: '紧急联系阈值',
  activity_immersion: '活动沉浸阈值',
  pride_reactive: '自尊反应阈值',
  valence_reactive: '效价反应阈值',
}

const thresholdItems = computed(() =>
  params.value
    ? Object.entries(params.value.jiwen.thresholds).map(([key, value]) => ({
        key,
        label: thresholdLabels[key] || key,
        value,
      }))
    : [],
)

const activityLabels: Record<string, string> = {
  reading: '阅读',
  search: '搜索',
  browse: '浏览',
  observe: '观察',
  none: '无活动',
}

const themeLabels: Record<string, string> = {
  identity: '身份/性格',
  music: '音乐品味',
  taste: '喜好/偏好',
  moment: '瞬间/事件',
  todo: '时效待办',
  default: '未分类',
}

async function load() {
  if (!props.characterId) return
  loading.value = true
  try {
    const r = await jiwenApi.getParams(props.characterId)
    params.value = r
  } catch (e) {
    showToast?.(
      `加载配置失败：${(e as Error).message || '未知错误'}`,
      'error',
      4000,
    )
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!props.characterId || !params.value) return
  saving.value = true
  try {
    const payload: Record<string, unknown> = {
      jiwen: {
        rates: params.value.jiwen.rates,
        thresholds: params.value.jiwen.thresholds,
        activities: params.value.jiwen.activities,
        fallback_templates: params.value.jiwen.fallback_templates.filter((t) => t.trim()),
        prompt_templates: params.value.jiwen.prompt_templates,
      },
      decay: {
        themes: params.value.decay.themes,
        should_forget_threshold: params.value.decay.should_forget_threshold,
      },
      summary: params.value.summary,
      session: params.value.session,
    }
    await jiwenApi.updateParams(props.characterId, payload)
    showToast?.('Jiwen 参数已保存', 'success', 2000)
  } catch (e) {
    showToast?.(
      `保存失败：${(e as Error).message || '未知错误'}`,
      'error',
      4000,
    )
  } finally {
    saving.value = false
  }
}

function addFallback() {
  if (!params.value) return
  params.value.jiwen.fallback_templates.push('')
}

function removeFallback(idx: number) {
  if (!params.value) return
  params.value.jiwen.fallback_templates.splice(idx, 1)
}

watch(() => props.characterId, (id) => {
  if (id) {
    load()
  } else {
    params.value = null
  }
}, { immediate: true })
</script>

<style scoped>
.jiwen-config {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.empty, .loading {
  padding: 30px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
}

.config-tabs {
  display: flex;
  gap: 4px;
  background: var(--bg-soft);
  padding: 4px;
  border-radius: var(--radius);
}
.tab-btn {
  flex: 1;
  padding: 7px 0;
  border-radius: 7px;
  font-size: 13px;
  color: var(--text-secondary);
  background: transparent;
  transition: background 0.15s, color 0.15s;
}
.tab-btn.active {
  background: var(--bg-card);
  color: var(--text);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.config-section h4 {
  margin: 0 0 4px 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.muted {
  color: var(--text-tertiary);
  font-size: 12px;
  margin-bottom: 12px;
}

.param-row {
  display: grid;
  grid-template-columns: 200px 1fr 80px;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
}
.param-label {
  display: flex;
  flex-direction: column;
  font-size: 13px;
  color: var(--text-secondary);
}
.param-key {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}
.param-slider {
  width: 100%;
}
.param-val {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
  text-align: right;
}

.theme-row {
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  margin-bottom: 10px;
}
.theme-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--accent);
  margin-bottom: 6px;
}

.fallback-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.input {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 13px;
  color: var(--text);
  width: 100%;
}

.config-actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
</style>
