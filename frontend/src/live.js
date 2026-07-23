// Keep the open UI in sync with stock changes that happen elsewhere — from the chat
// assistant, MCP/Home Assistant, or another device/tab. Three triggers:
//  1. an in-app signal (`dataChanged()`) the chat fires after a mutating action,
//  2. refetch when the tab regains focus/visibility (catches external changes),
//  3. a light poll while the tab is visible (near-live for the active view).
import { ref, watch, onMounted, onUnmounted } from 'vue'

export const dataVersion = ref(0)

/** Broadcast that inventory data changed so every live view refetches. */
export function dataChanged() { dataVersion.value += 1 }

/**
 * Wire a view's `reload` fn to the live triggers. `reload` should be SILENT
 * (no loading spinner) so background refreshes don't flicker. Cleans up on unmount.
 */
export function useLiveRefresh(reload, { poll = 30000 } = {}) {
  let timer = null
  const onFocus = () => reload()
  const onVisible = () => { if (document.visibilityState === 'visible') reload() }
  const stop = watch(dataVersion, () => reload())

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onFocus)
    if (poll) timer = setInterval(onVisible, poll)   // onVisible only fetches when visible
  })
  onUnmounted(() => {
    stop()
    document.removeEventListener('visibilitychange', onVisible)
    window.removeEventListener('focus', onFocus)
    if (timer) clearInterval(timer)
  })
}
