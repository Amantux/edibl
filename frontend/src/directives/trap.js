// v-trap="closeFn" — makes an element behave as an accessible modal dialog:
// focuses it on open, traps Tab within it, closes on Escape (calls closeFn),
// restores focus to the previously-focused element on close, and locks body scroll.
// Retrofits a11y onto existing inline modals without restructuring their markup.
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),' +
  'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

export const trap = {
  mounted(el, binding) {
    el._prevFocus = document.activeElement
    el.setAttribute('role', el.getAttribute('role') || 'dialog')
    el.setAttribute('aria-modal', 'true')
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1')

    el._onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        if (typeof binding.value === 'function') binding.value()
        return
      }
      if (e.key !== 'Tab') return
      const items = [...el.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null)
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    el.addEventListener('keydown', el._onKey)
    document.body.style.overflow = 'hidden'
    // Focus the first field (or the dialog itself) once rendered.
    requestAnimationFrame(() => {
      const focusables = [...el.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null)
      const target = el.querySelector('[autofocus]') || focusables[0] || el
      try { target.focus() } catch (_) { /* noop */ }
    })
  },
  unmounted(el) {
    el.removeEventListener('keydown', el._onKey)
    document.body.style.overflow = ''
    try { el._prevFocus && el._prevFocus.focus() } catch (_) { /* element gone */ }
  },
}
