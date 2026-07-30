// The chat assistant ends a completed bug-report walkthrough with this marker; the UI
// strips it from what it shows and opens the bug reporter prefilled with the summary.
export const BUG_MARKER = '[[REPORT_BUG]]'

// For LIVE STREAMING display: remove any complete marker AND trim a trailing PARTIAL
// prefix of it (e.g. "…text [[REPO" → "…text"), so the marker never flashes on screen
// as it streams in character by character.
export function hideMarker(text) {
  if (!text) return text
  let t = text.split(BUG_MARKER).join('')
  for (let i = BUG_MARKER.length - 1; i > 0; i--) {
    if (t.endsWith(BUG_MARKER.slice(0, i))) { t = t.slice(0, -i); break }
  }
  return t.replace(/\s+$/, '')
}

// For the FINAL reply (stream `done` / POST): strip complete markers only — the text
// is final, so there's no trailing partial to trim. Returns the cleaned content plus a
// `summary` (the clean text) when a marker was present, else null.
export function finalizeReply(text) {
  const content = (text || '').split(BUG_MARKER).join('').trim()
  const summary = (text || '').includes(BUG_MARKER) ? content : null
  return { content, summary }
}
