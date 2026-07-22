// Tiny cross-component bus so anything (e.g. the stock omnibox) can pop open the
// floating assistant with a prefilled question. Module-scoped refs, no store lib.
import { ref } from 'vue'

export const chatOpen = ref(false)
export const chatPrefill = ref('')

// Open the assistant, optionally seeding the input with `text`.
export function askEdibl(text = '') {
  chatPrefill.value = text
  chatOpen.value = true
}
