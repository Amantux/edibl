// Tiny cross-component bus so anything (e.g. the stock omnibox) can pop open the
// floating assistant with a prefilled question. Module-scoped refs, no store lib.
import { ref } from 'vue'

export const chatOpen = ref(false)
export const chatPrefill = ref('')
// Set by the assistant's bug-report walkthrough to open ReportBug prefilled.
export const bugReport = ref(null)   // { description, type? } | null
export function openBugReport(payload) { bugReport.value = payload }

// Open the assistant, optionally seeding the input with `text`.
export function askEdibl(text = '') {
  chatPrefill.value = text
  chatOpen.value = true
}
