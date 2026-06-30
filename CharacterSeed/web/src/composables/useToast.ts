import { ref } from 'vue'

/**
 * Toast 通知系统
 *
 * 用法：
 *   const { toasts, show, dismiss } = useToast()
 *   show('操作成功', 'success', 2500)
 *
 * 通过 provide/inject 暴露 show() 给子组件：
 *   app.provide('showToast', show)
 *   const showToast = inject('showToast') as ToastShowFn
 */

export type ToastType = 'success' | 'error' | 'info' | 'warning'

export interface Toast {
  id: number
  message: string
  type: ToastType
  duration: number
}

const toasts = ref<Toast[]>([])
let nextId = 1

export function useToast() {
  function show(
    message: string,
    type: ToastType = 'info',
    duration: number = 3000,
  ): number {
    const id = nextId++
    toasts.value.push({ id, message, type, duration })
    if (duration > 0) {
      setTimeout(() => dismiss(id), duration)
    }
    return id
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  return { toasts, show, dismiss }
}

export type ToastShowFn = (
  message: string,
  type?: ToastType,
  duration?: number,
) => number
