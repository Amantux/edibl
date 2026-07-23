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

// --- myMeal connection ---
const mm = ref(null)
const mmForm = ref({ url: '', token: '' })
const mmMsg = ref('')
const mmBusy = ref(false)
async function loadMyMeal() {
  try {
    mm.value = await api.get('/integrations/mymeal')
    mmForm.value = { url: mm.value.url || '', token: '' }
  } catch (e) { mm.value = { source: 'none' } }
}
function _testMsg(r) {
  return !r.configured ? '⚠️ No myMeal URL set — paste a connect link, pick a discovered add-on, or enter the URL below.'
    : r.reachable ? `✓ Connected — myMeal has ${r.items} planned ingredient(s).`
      : '⚠️ Saved, but can’t reach it: ' + (r.error || 'no response')
        + '. Check the URL/token, or that myMeal is running.'
}
async function saveMyMeal() {
  mmBusy.value = true; mmMsg.value = 'Saving…'
  try {
    const body = { url: mmForm.value.url }
    if (mmForm.value.token) body.token = mmForm.value.token
    mm.value = await api.put('/integrations/mymeal', body)
    mmForm.value = { url: mm.value.url || '', token: '' }
    // Verify in the same step so the user knows immediately whether it works.
    mmMsg.value = 'Saved — testing…'
    mmMsg.value = _testMsg(await api.post('/integrations/mymeal/test'))
  } catch (e) { mmMsg.value = '⚠️ ' + (e.message || 'save failed') } finally { mmBusy.value = false }
}
async function testMyMeal() {
  mmBusy.value = true; mmMsg.value = 'Testing…'
  try {
    mmMsg.value = _testMsg(await api.post('/integrations/mymeal/test'))
  } catch (e) { mmMsg.value = '⚠️ ' + (e.message || 'error') } finally { mmBusy.value = false }
}
async function pullMyMeal() {
  mmBusy.value = true; mmMsg.value = 'Pulling…'
  try {
    const r = await api.post('/integrations/mymeal/pull')
    mmMsg.value = `✓ Pulled ${r.pulled} planned item(s) from myMeal.`
  } catch (e) { mmMsg.value = '⚠️ ' + (e.message || 'pull failed') } finally { mmBusy.value = false }
}
const mmCandidates = ref([])
async function discoverMyMeal() {
  mmBusy.value = true; mmMsg.value = 'Searching Home Assistant…'; mmCandidates.value = []
  try {
    const r = await api.post('/integrations/mymeal/discover')
    mmCandidates.value = r.candidates || []
    if (!r.available) mmMsg.value = 'Add-on discovery only works when Edibl runs as a Home Assistant add-on.'
    else if (!mmCandidates.value.length) mmMsg.value = 'No myMeal add-on found — enter the URL manually.'
    else mmMsg.value = `Found ${mmCandidates.value.length} add-on(s) — pick one below.`
  } catch (e) { mmMsg.value = '⚠️ ' + (e.message || 'error') } finally { mmBusy.value = false }
}
// Picking a discovered add-on connects in one click (fill → save → verify).
async function useCandidate(cnd) {
  mmForm.value.url = cnd.url
  mmMsg.value = `Connecting “${cnd.name}”…`
  await saveMyMeal()
}

// ── Access & keys: mint/list/revoke tokens + connect-link sharing ────────────
const tokens = ref([])
const newTokenName = ref('')
const minted = ref(null)            // { token, name } — raw token, shown once
const connectUrl = ref('')          // address other apps use to reach this Edibl
const keysBusy = ref(false)
const keysMsg = ref('')
async function loadTokens() { try { tokens.value = await api.get('/tokens') } catch (e) { /* auth off / optional */ } }
async function mintToken() {
  keysBusy.value = true; keysMsg.value = ''
  try {
    const r = await api.post('/tokens', { name: newTokenName.value || 'Connected app' })
    minted.value = { token: r.token, name: r.name }
    newTokenName.value = ''
    await loadTokens()
  } catch (e) { keysMsg.value = '⚠️ ' + (e.message || 'could not create token') } finally { keysBusy.value = false }
}
async function revokeToken(id) {
  if (!confirm('Revoke this token? Anything using it loses access.')) return
  try { await api.del('/tokens/' + id); await loadTokens() } catch (e) { keysMsg.value = '⚠️ ' + (e.message || 'revoke failed') }
}
function encodeConnect(app, url, token) {
  return app + '-connect:' + btoa(unescape(encodeURIComponent(JSON.stringify({ app, url, token, v: 1 }))))
}
function decodeConnect(str, expectApp) {
  const m = /^([a-z]+)-connect:(.+)$/.exec((str || '').trim())
  if (!m) return null
  try {
    const obj = JSON.parse(decodeURIComponent(escape(atob(m[2]))))
    return (!expectApp || obj.app === expectApp) ? obj : null
  } catch (e) { return null }
}
const ediblConnectLink = computed(() =>
  minted.value ? encodeConnect('edibl', connectUrl.value || (typeof window !== 'undefined' ? window.location.origin : ''), minted.value.token) : '')
async function copyText(text, label) {
  try { await navigator.clipboard.writeText(text); keysMsg.value = `✓ ${label} copied.` }
  catch (e) { keysMsg.value = 'Copy failed — select the text and copy manually.' }
}
// Paste a myMeal connect link — fills URL + token AND connects in one step.
async function pasteMymealConnect(str) {
  const obj = decodeConnect(str, 'mymeal')
  if (!obj) { mmMsg.value = '⚠️ That doesn’t look like a myMeal connect link.'; return }
  mmForm.value.url = obj.url || mmForm.value.url
  mmForm.value.token = obj.token || ''
  mmMsg.value = 'Connecting from the link…'
  await saveMyMeal()
}
const mmDiag = ref('')
async function diagnoseMyMeal() {
  mmBusy.value = true; mmDiag.value = ''
  try {
    mmDiag.value = JSON.stringify(await api.get('/integrations/mymeal/discover/debug'), null, 2)
  } catch (e) { mmDiag.value = 'Error: ' + (e.message || 'failed') } finally { mmBusy.value = false }
}

onMounted(() => {
  loadSettings(); loadMyMeal(); loadTokens()
  if (typeof window !== 'undefined') connectUrl.value = window.location.origin
})
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

async function resetSettings() {
  saving.value = true; saved.value = ''
  try {
    s.value = await api.del('/assistant/settings')
    setForm(s.value)
    saved.value = '↩ Reset — now using the add-on / env config.'
  } catch (e) { saved.value = '⚠️ ' + (e.message || 'reset failed') } finally { saving.value = false }
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
async function backupDb() {
  const res = await fetch(apiUrl('/export/backup.db'), { headers: getToken() ? { Authorization: getToken() } : {} })
  if (!res.ok) return
  const stamp = new Date().toISOString().slice(0, 10)
  triggerBlob(await res.blob(), `edibl-backup-${stamp}.db`)
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
        <span v-else-if="s.source==='ui'"> Currently set here (overrides the add-on config).</span>
      </p>
      <p class="muted" style="font-size:.8rem;margin-top:-6px">
        Saved here, changes apply immediately. Changing the add-on
        <strong>Configuration</strong> tab instead needs an add-on restart to take effect.</p>
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
        <button v-if="s.source==='ui'" class="ghost sm" :disabled="saving" @click="resetSettings"
          title="Discard the value set here and use the add-on / env config">↩ Reset to add-on default</button>
        <button :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
      </div>
    </div>
    <div v-else class="muted">Loading…</div>
  </div>

  <div class="card">
    <h2>myMeal</h2>
    <p class="muted" style="margin-top:0">Connect to <strong>myMeal</strong> (recipes &amp; meal plans). Edibl pulls the ingredients your planned meals need and reconciles them against what's actually in stock, so you see what to buy.</p>
    <div v-if="mm">
      <div class="row wrap" style="margin-bottom:10px">
        <button class="secondary sm" :disabled="mmBusy" @click="discoverMyMeal">🔍 Find myMeal add-on</button>
        <button class="secondary sm" :disabled="mmBusy" @click="diagnoseMyMeal" title="Show what discovery tried (for troubleshooting)">🔧 Diagnose</button>
      </div>
      <pre v-if="mmDiag" style="max-height:220px;overflow:auto;background:var(--surface-raised,#f6f6f6);padding:10px;border-radius:6px;font-size:.78rem;margin-bottom:10px">{{ mmDiag }}</pre>
      <div v-if="mmCandidates.length" class="row wrap" style="margin-bottom:10px;gap:6px">
        <button v-for="cnd in mmCandidates" :key="cnd.slug" class="chip" style="cursor:pointer;border:none"
          @click="useCandidate(cnd)">{{ cnd.name }} · {{ cnd.hostname }}:{{ cnd.port }}{{ cnd.running ? '' : ' (stopped)' }}</button>
      </div>
      <label class="field"><span>Paste a myMeal <strong>connect link</strong> (fills URL + token)</span>
        <input placeholder="mymeal-connect:… — from myMeal → Settings → Access &amp; keys"
          @change="(e)=>{ pasteMymealConnect(e.target.value); e.target.value='' }" /></label>
      <label class="field"><span>myMeal URL</span>
        <input v-model="mmForm.url" placeholder="http://mymeal:8000 or an add-on hostname" /></label>
      <label class="field"><span>API token {{ mm.hasToken ? '— saved, leave blank to keep' : '(optional)' }}</span>
        <input v-model="mmForm.token" type="password" :placeholder="mm.hasToken ? '•••••••••• saved' : 'token'" /></label>
      <div class="row wrap" style="align-items:center;gap:10px">
        <button :disabled="mmBusy" @click="saveMyMeal">Save</button>
        <button class="secondary" :disabled="mmBusy || !mmForm.url" @click="testMyMeal">Test connection</button>
        <button class="secondary" :disabled="mmBusy" @click="pullMyMeal">⬇️ Pull plan now</button>
        <span v-if="mmMsg" class="muted" style="font-size:.85rem">{{ mmMsg }}</span>
      </div>
    </div>
    <div v-else class="muted">Loading…</div>
  </div>

  <!-- Access & keys -->
  <div class="card">
    <h2>🔑 Access &amp; keys</h2>
    <p class="muted" style="margin-top:0">Long-lived tokens for other apps or Home Assistant to reach Edibl's API — needed only when Edibl runs <strong>standalone</strong> or is reached across the network (behind HA Ingress, siblings connect without a key). Generate one, then hand it to the other app with a <strong>connect link</strong>.</p>

    <label class="field"><span>Address other apps use to reach Edibl</span>
      <input v-model="connectUrl" placeholder="https://edibl.example.com" /></label>
    <div class="row" style="align-items:flex-end;gap:8px">
      <label class="field" style="flex:1"><span>New token name (what's it for?)</span>
        <input v-model="newTokenName" placeholder="e.g. myMeal, HA MCP" @keyup.enter="mintToken" /></label>
      <button :disabled="keysBusy" @click="mintToken" style="height:38px">Generate</button>
    </div>

    <div v-if="minted" style="border:1px solid var(--primary,#2f9e57);border-radius:8px;padding:10px 12px;margin-top:10px;background:rgba(47,158,87,.10)">
      <p style="margin:0 0 6px"><strong>New token “{{ minted.name }}” — copy it now, it won't be shown again.</strong></p>
      <code style="display:block;word-break:break-all;background:var(--surface-raised,#f6f6f6);padding:6px 8px;border-radius:6px;font-size:.8rem">{{ minted.token }}</code>
      <div class="row wrap" style="gap:8px;margin-top:8px">
        <button class="secondary sm" @click="copyText(minted.token, 'Token')">Copy token</button>
        <button class="sm" @click="copyText(ediblConnectLink, 'Connect link')">🔗 Copy connect link</button>
        <button class="ghost sm" @click="minted=null">Done</button>
      </div>
      <p class="muted" style="font-size:.78rem;margin:8px 0 0">Paste the connect link into the other app's Edibl connection (fills its URL + token in one step).</p>
    </div>

    <table v-if="tokens.length" style="width:100%;margin-top:12px;font-size:.9rem">
      <thead><tr><th style="text-align:left">Name</th><th style="text-align:left">Hint</th><th></th></tr></thead>
      <tbody>
        <tr v-for="t in tokens" :key="t.id">
          <td>{{ t.name }}</td><td class="muted"><code>{{ t.hint }}…</code></td>
          <td style="text-align:right"><button class="ghost sm" @click="revokeToken(t.id)">Revoke</button></td>
        </tr>
      </tbody>
    </table>
    <p v-if="keysMsg" class="muted" style="font-size:.85rem;margin-top:8px">{{ keysMsg }}</p>
  </div>

  <div class="card">
    <h2>Export</h2>
    <p class="muted" style="margin-top:0">Download a portable snapshot of your inventory — to keep, or to move to another Edibl instance. (Home Assistant already backs up the add-on's storage automatically.)</p>
    <div class="row wrap">
      <button @click="exportJson">⬇️ Export JSON (full)</button>
      <button class="secondary" @click="exportCsv">⬇️ Stock as CSV</button>
      <button class="secondary" @click="backupDb" title="A consistent copy of the whole database file">💾 Backup database</button>
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
