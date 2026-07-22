// Global, accessible notification store — one source of truth for transient
// feedback, replacing per-view toast refs and alert()/prompt() confirmations.
import { ref } from 'vue'

export const toasts = ref([])
let seq = 0

/**
 * Show a toast.
 * @param {string} message
 * @param {{kind?: 'info'|'success'|'error', timeout?: number,
 *          action?: {label: string, run: () => void}}} [opts]
 * Errors persist until dismissed; others auto-dismiss. Returns the toast id.
 */
export function toast(message, opts = {}) {
  const id = ++seq
  const kind = opts.kind || 'info'
  const timeout = opts.timeout ?? (kind === 'error' ? 0 : opts.action ? 8000 : 4000)
  toasts.value.push({ id, message, kind, action: opts.action || null })
  if (timeout) setTimeout(() => dismiss(id), timeout)
  return id
}

export function dismiss(id) {
  toasts.value = toasts.value.filter((t) => t.id !== id)
}

export const ui = {
  toast,
  dismiss,
  info: (m, o) => toast(m, { ...o, kind: 'info' }),
  success: (m, o) => toast(m, { ...o, kind: 'success' }),
  error: (m, o) => toast(m, { ...o, kind: 'error' }),
}
