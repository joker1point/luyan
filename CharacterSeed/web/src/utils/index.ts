/**
 * 工具函数：JSON 安全解析、日期格式化、Markdown 简化
 */

export function safeJsonParse<T>(s: string | null | undefined, fallback: T): T {
  if (!s) return fallback
  try { return JSON.parse(s) as T } catch { return fallback }
}

export function formatDate(s: string | null | undefined, withTime = true): string {
  if (!s) return ''
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  const pad = (n: number) => String(n).padStart(2, '0')
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  if (!withTime) return date
  return `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function formatRelative(s: string | null | undefined): string {
  if (!s) return ''
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  const diff = Date.now() - d.getTime()
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return '刚刚'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} 天前`
  return formatDate(s, false)
}

/** 简单 Markdown 渲染（仅支持粗体/斜体/标题/列表/代码，避免引第三方库） */
export function renderMarkdown(src: string): string {
  if (!src) return ''
  let html = src
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  // 代码块
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, _lang, code) =>
    `<pre><code>${code.trim()}</code></pre>`)
  // 行内代码
  html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>')
  // 标题
  html = html.replace(/^###### (.*$)/gm, '<h6>$1</h6>')
  html = html.replace(/^##### (.*$)/gm, '<h5>$1</h5>')
  html = html.replace(/^#### (.*$)/gm, '<h4>$1</h4>')
  html = html.replace(/^### (.*$)/gm, '<h3>$1</h3>')
  html = html.replace(/^## (.*$)/gm, '<h2>$1</h2>')
  html = html.replace(/^# (.*$)/gm, '<h1>$1</h1>')
  // 引用
  html = html.replace(/^&gt; (.*$)/gm, '<blockquote>$1</blockquote>')
  // 粗体 + 斜体
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>')
  // 列表
  html = html.replace(/^- (.*$)/gm, '<li>$1</li>')
  html = html.replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`)
  // 段落（双换行分段，单换行替换为 <br>）
  html = html
    .split(/\n{2,}/)
    .map(p => /^<(h\d|ul|pre|blockquote)/.test(p.trim()) ? p : `<p>${p.replace(/\n/g, '<br>')}</p>`)
    .join('\n')
  return html
}

/** 时段色 CSS 变量名（自动适配主题） */
export function timePeriodVar(t: string | null | undefined): string {
  switch (t) {
    case 'morning':   return 'var(--period-morning)'
    case 'afternoon': return 'var(--period-afternoon)'
    case 'evening':   return 'var(--period-evening)'
    case 'night':     return 'var(--period-night)'
    default: return 'var(--text-tertiary)'
  }
}

/** 状态色 CSS 变量名 */
export function statusVar(s: string): string {
  switch (s) {
    case 'pending':   return 'var(--warning)'
    case 'active':    return 'var(--info)'
    case 'completed': return 'var(--success)'
    default: return 'var(--text-tertiary)'
  }
}

/**
 * 保留旧 API 以兼容直接渲染场景（如 statusColor(s) 仍返回 hex 字符串）
 * 推荐新代码改用 statusVar(s) 配合 CSS 变量。
 */
export function timePeriodColor(t: string | null | undefined): string {
  switch (t) {
    case 'morning':   return '#f59e0b'
    case 'afternoon': return '#3b82f6'
    case 'evening':   return '#8b5cf6'
    case 'night':     return '#1f2937'
    default: return '#6b7280'
  }
}

export function statusColor(s: string): string {
  switch (s) {
    case 'pending':   return '#f59e0b'
    case 'active':    return '#3b82f6'
    case 'completed': return '#10b981'
    default: return '#6b7280'
  }
}

/** 高亮事件类型的中文标签 */
export function eventTypeLabel(t: string): string {
  switch (t) {
    case 'schedule_action':       return '日程行动'
    case 'scene_event':           return '场景事件'
    case 'character_initiative':  return '主动行动'
    case 'player_dialogue':       return '玩家对话'
    default: return t
  }
}

/** 时段中文标签 */
export function timePeriodLabel(t: string | null | undefined): string {
  switch (t) {
    case 'morning':   return '清晨'
    case 'afternoon': return '午后'
    case 'evening':   return '黄昏'
    case 'night':     return '夜晚'
    default: return t ?? ''
  }
}
