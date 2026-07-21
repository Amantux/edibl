<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, apiUrl, getToken } from '../api'

const importing = ref(false)
const result = ref('')
const s = ref(null)                       // /assistant/settings view
const form = ref({ provider: '', baseUrl: '', model: '', apiKey: '' })
const saving = ref(false)
const saved = ref('')

const providerLabels = { '': '— none (disabled) —', ollama: 'Ollama (local)',
  openai: 'OpenAI-compatible', anthropic: 'Anthropic',
  homeassistant: "Home Assistant's agent" }

onMounted(loadSettings)
async function loadSettings() {
  try {
    s.value = await api.get('/assistant/settings')
    form.value = { provider: s.value.provider, baseUrl: s.value.baseUrl, model: s.value.model, apiKey: '' }
  } catch (e) { s.value = { providers: [''], defaults: {}, needsKey: {} } }
}
const def = computed(() => (s.value?.defaults || {})[form.value.provider] || {})
const needsKey = computed(() => (s.value?.needsKey || {})[form.value.provider])

async function save() {
  saving.value = true; saved.value = ''
  try {
    const body = { provider: form.value.provider, baseUrl: form.value.baseUrl, model: form.value.model }
    if (form.value.apiKey) body.apiKey = form.value.apiKey
    s.value = await api.put('/assistant/settings', body)
    form.value = { provider: s.value.provider, baseUrl: s.value.baseUrl, model: s.value.model, apiKey: '' }
    saved.value = '✓ Saved — the chat assistant will use this.'
  } catch (e) { saved.value = '⚠️ ' + (e.message || 'save failed') } finally { saving.value = false }
}

function triggerBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}
async function exportJson() {
  triggerBlob(new Blob([JSON.stringify(await api.get('/export'), null, 2)], { type: 'application/json' }), 'edibl-export.json')
}
async function exportCsv() {
  const res = await fetch(apiUrl('/export/stock.csv'), { headers: getToken() ? { Authorization: getToken() } : {} })
  triggerBlob(await res.blob(), 'edibl-stock.csv')
}
async function importFile(e) {
  const file = e.target.files?.[0]; e.target.value = ''
  if (!file) return
  importing.value = true; result.value = ''
  try {
    const c = (await api.post('/import', JSON.parse(await file.text()))).imported
    result.value = `✓ Imported ${c.products} products, ${c.locations} locations, ${c.stock} stock lots, ${c.shopping} shopping items.`
  } catch (err) { result.value = '⚠️ Import failed: ' + (err.message || 'invalid file') } finally { importing.value = false }
}
</script>

<template>
  <div class="page-head"><h1>⚙️ Settings</h1></div>

  <div class="card">
    <h2>Chat assistant</h2>
    <div v-if="s">
      <p class="muted" style="margin-top:0">Pick the LLM that powers the chat &amp; receipt extraction. Set it here, or in Home Assistant → <strong>Settings → Add-ons → Edibl → Configuration</strong> — either is remembered.
        <span v-if="s.source==='addon'"> Currently from the add-on config.</span>
        <span v-else-if="s.source==='ui'"> Currently set here.</span>
      </p>
      <div class="row wrap" style="gap:8px;margin-bottom:12px">
        <span class="badge" :class="s.enabled ? 'fresh' : 'expired'">{{ s.enabled ? 'connected' : 'not configured' }}</span>
        <span v-if="s.enabled" class="chip">{{ s.tools ? 'full chat CRUD' : 'completion-only' }}</span>
      </div>

      <label class="field"><span>Provider</span>
        <select v-model="form.provider">
          <option v-for="p in s.providers" :key="p" :value="p">{{ providerLabels[p] || p }}</option>
        </select></label>

      <template v-if="form.provider && form.provider !== 'homeassistant'">
        <div class="row wrap">
          <label class="field" style="flex:1;min-width:200px"><span>Base URL</span>
            <input v-model="form.baseUrl" :placeholder="def.baseUrl || ''" /></label>
          <label class="field" style="flex:1;min-width:160px"><span>Model</span>
            <input v-model="form.model" :placeholder="def.model || ''" /></label>
        </div>
        <label v-if="needsKey" class="field"><span>API key {{ s.hasApiKey ? '(saved — leave blank to keep)' : '' }}</span>
          <input v-model="form.apiKey" type="password" :placeholder="s.hasApiKey ? '•••••••••• saved' : 'sk-…'" /></label>
      </template>
      <p v-else-if="form.provider === 'homeassistant'" class="muted" style="font-size:.85rem;margin-top:-4px">
        Reuses Home Assistant's own conversation agent — no URL or key needed. Completion-only (great for receipt extraction; full chat-CRUD needs ollama/openai/anthropic).
      </p>

      <div class="row" style="justify-content:flex-end;align-items:center;gap:10px;margin-top:6px">
        <span v-if="saved" class="muted" style="font-size:.85rem">{{ saved }}</span>
        <button :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
      </div>
    </div>
    <div v-else class="muted">Loading…</div>
  </div>

  <div class="card">
    <h2>Export</h2>
    <p class="muted" style="margin-top:0">Download a portable snapshot of your inventory — to keep, or to move to another Edibl instance. (Home Assistant already backs up the add-on's storage automatically.)</p>
    <div class="row wrap">
      <button @click="exportJson">⬇️ Export JSON (full)</button>
      <button class="secondary" @click="exportCsv">⬇️ Stock as CSV</button>
    </div>
  </div>

  <div class="card">
    <h2>Import</h2>
    <p class="muted" style="margin-top:0">Restore from an exported JSON file. Import is <strong>additive</strong> — it creates products, locations, stock, and shopping items that don't already exist, and never deletes anything.</p>
    <label class="secondary" style="cursor:pointer;display:inline-block;padding:9px 15px;border-radius:9px">
      {{ importing ? 'Importing…' : '⬆️ Choose export file' }}
      <input type="file" hidden accept=".json,application/json" :disabled="importing" @change="importFile" />
    </label>
    <p v-if="result" style="margin-top:10px">{{ result }}</p>
  </div>
</template>
