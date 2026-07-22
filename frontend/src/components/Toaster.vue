<script setup>
// Renders the global toast queue with a screen-reader live region. Errors are
// announced assertively and stay until dismissed; others are polite + auto-dismiss.
import { toasts, dismiss } from '../ui'
</script>

<template>
  <div class="toaster" aria-live="polite" aria-relevant="additions">
    <div v-for="t in toasts" :key="t.id" class="toast-item" :class="t.kind"
      :role="t.kind === 'error' ? 'alert' : 'status'">
      <span class="tmsg">{{ t.message }}</span>
      <button v-if="t.action" class="taction" @click="t.action.run(); dismiss(t.id)">{{ t.action.label }}</button>
      <button class="tclose" aria-label="Dismiss" @click="dismiss(t.id)">✕</button>
    </div>
  </div>
</template>

<style scoped>
.toaster {
  position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%);
  z-index: 200; display: flex; flex-direction: column; gap: 8px; align-items: center;
  width: max-content; max-width: min(92vw, 460px); pointer-events: none;
}
.toast-item {
  pointer-events: auto; display: flex; align-items: center; gap: 10px;
  background: var(--surface); color: var(--text); border: 1px solid var(--border);
  border-left: 3px solid var(--muted); border-radius: var(--radius-sm);
  box-shadow: var(--popover-shadow); padding: 10px 12px; font-size: .9rem;
  animation: toastIn .16s ease;
}
.toast-item.success { border-left-color: var(--success); }
.toast-item.error { border-left-color: var(--danger); }
.tmsg { flex: 1; }
.taction {
  background: transparent; color: var(--accent); border: 1px solid var(--accent);
  border-radius: 6px; padding: 2px 10px; font-size: .8rem; cursor: pointer; flex: none;
}
.taction:hover { background: var(--accent-soft); }
.tclose {
  background: transparent; color: var(--muted); border: none; padding: 2px 6px;
  font-size: .9rem; line-height: 1; cursor: pointer; flex: none; border-radius: 6px;
}
.tclose:hover { background: var(--surface-2); color: var(--text); }
@keyframes toastIn { from { transform: translateY(8px); opacity: 0; } to { transform: none; opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .toast-item { animation: none; } }
@media (max-width: 560px) { .toaster { left: 12px; right: 12px; transform: none; max-width: none; } }
</style>
