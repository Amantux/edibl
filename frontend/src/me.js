import { ref } from 'vue'
import { api } from './api'

// The current user + role, shared across the nav and the router guard. The
// server enforces roles regardless; this only hides owner-only UI from members.
export const me = ref(null)

export async function ensureMe() {
  try {
    me.value = await api.get('/me')
  } catch (e) {
    if (me.value === null) me.value = { isOwner: false }
  }
  return me.value
}
