<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api, apiUrl, getToken } from '../api'

const importing = ref(false)
const result = ref('')
const s = ref(null)                       // /assistant/settings view
const form = ref({ provider: '', baseUrl: '', model: '', apiKey: '', agentId: '' })
const saving = ref(false)
const saved = ref('')
const models = ref([])
const modelsMsg = ref('')
const loadingModels = ref(false)

const providerLabels = { '': '— none (disabled) —', ollama: 'Ollama',
  openai: 'OpenAI-compatible', anthropic: 'Anthropic',
  homeassistant: "Home Assistant's agent" }

onMounted(loadSettings)
async function loadSettings() {
  try {
    s.value = await api.get('/assistant/settings')
    setForm(s.value)
    if (canList.value) loadModels()
  } catch (e) { s.value = { providers: [''], defaults: {}, needsKey: {}, canListModels: {} } }
}
function setForm(v) {
  form.value = { provider: v.provider, baseUrl: v.baseUrl, model: v.model, apiKey: '', agentId: v.agentId || '' }
}
const def = computed(() => (s.value?.defaults || {})[form.value.provider] || {})
const needsKey = computed(() => (s.value?.needsKey || {})[form.value.provider])
const showKey = computed(() => ['ollama', 'openai', 'anthropic'].includes(form.value.provider))
const canList = computed(() => (s.value?.canListModels || {})[form.value.provider])

watch(() => form.value.provider, () => { models.value = []; modelsMsg.value = ''; if (canList.value) loadModels() })

async function loadModels() {
  if (!canList.value) return
  loadingModels.value = true; modelsMsg.value = ''
  try {
    const body = { provider: form.value.provider, baseUrl: form.value.baseUrl }
    if (form.value.apiKey) body.apiKey = form.value.apiKey
    const r = await api.post('/assistant/models', body)
    models.value = r.models || []
    if (r.error) modelsMsg.value = 'Could not list models: ' + r.error
    else if (!models.value.length) modelsMsg.value = 'No models found on this server.'
    else modelsMsg.value = `${models.value.length} models available`
  } catch (e) { modelsMsg.value = 'Could not reach the server.' } finally { loadingModels.value = false }
}

async function save() {
  saving.value = true; saved.value = ''
  try {
    const body = { provider: form.value.provider, baseUrl: form.value.baseUrl,
      model: form.value.model, agentId: form.value.agentId }
    if (form.value.apiKey) body.apiKey = form.value.apiKey
    s.value = await api.put('/assistant/settings', body)
    setForm(s.value)
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
        <label class="field"><span>Base URL</span>
          <input v-model="form.baseUrl" :placeholder="def.baseUrl || ''" @change="canList && loadModels()" /></label>

        <label v-if="showKey" class="field">
          <span>API key {{ needsKey ? '' : '(optional)' }} {{ s.hasApiKey ? '— saved, leave blank to keep' : '' }}</span>
          <input v-model="form.apiKey" type="password" :placeholder="s.hasApiKey ? '•••••••••• saved' : (needsKey ? 'sk-…' : 'only if your Ollama needs one')" /></label>

        <label class="field"><span>Model
            <button v-if="canList" class="ghost sm" type="button" style="float:right;padding:0 6px"
              :disabled="loadingModels" @click="loadModels">{{ loadingModels ? '…' : '↻ Load models' }}</button></span>
          <input v-model="form.model" list="assistant-models" :placeholder="def.model || 'model name'" />
          <datalist id="assistant-models"><option v-for="m in models" :key="m" :value="m" /></datalist></label>
        <p v-if="modelsMsg" class="muted" style="font-size:.8rem;margin-top:-6px">{{ modelsMsg }}</p>
      </template>

      <template v-else-if="form.provider === 'homeassistant'">
        <p class="muted" style="font-size:.85rem;margin-top:-4px">
          Reuses Home Assistant's own conversation agent — no URL or key needed. Completion-only (great for receipt extraction; full chat-CRUD needs ollama/openai/anthropic).
        </p>
        <label class="field"><span>Conversation agent (optional)</span>
          <input v-model="form.agentId" placeholder="e.g. conversation.ollama — blank = HA default" /></label>
      </template>

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
