<template>
  <div class="toast-container">
    <TransitionGroup name="toast" tag="div">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="toast"
        :class="`toast-${toast.type}`"
        @click="dismiss(toast.id)"
      >
        <span class="toast-icon" aria-hidden="true">
          {{ iconFor(toast.type) }}
        </span>
        <span class="toast-msg">{{ toast.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
import type { Toast } from '@/composables/useToast'

defineProps<{
  toasts: Toast[]
  dismiss: (id: number) => void
}>()

function iconFor(type: Toast['type']): string {
  switch (type) {
    case 'success': return '✅'
    case 'error': return '❌'
    case 'warning': return '⚠️'
    default: return 'ℹ️'
  }
}
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none; /* allow container to be transparent to clicks */
}

.toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  min-width: 220px;
  max-width: 360px;
  border: 1px solid transparent;
  backdrop-filter: blur(6px);
}

.toast-icon {
  font-size: 16px;
  flex-shrink: 0;
  line-height: 1;
}

.toast-msg {
  flex: 1;
  line-height: 1.45;
  word-break: break-word;
}

.toast-success {
  background: var(--success-soft, #d1fae5);
  color: var(--success, #065f46);
  border-color: color-mix(in srgb, var(--success, #10b981) 30%, transparent);
}
.toast-error {
  background: var(--danger-soft, #fee2e2);
  color: var(--danger, #991b1b);
  border-color: color-mix(in srgb, var(--danger, #ef4444) 30%, transparent);
}
.toast-info {
  background: var(--info-soft, #e0e7ff);
  color: var(--info, #3730a3);
  border-color: color-mix(in srgb, var(--info, #6366f1) 30%, transparent);
}
.toast-warning {
  background: var(--warning-soft, #fef3c7);
  color: var(--warning, #92400e);
  border-color: color-mix(in srgb, var(--warning, #f59e0b) 30%, transparent);
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}
.toast-enter-from {
  opacity: 0;
  transform: translateX(40px);
}
.toast-leave-to {
  opacity: 0;
  transform: translateX(40px);
}
</style>
