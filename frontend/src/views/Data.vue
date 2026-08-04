<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { api } from '../api'
import { useJobRunner } from '../composables/useJobRunner'
import { ui } from '../ui'

const s = ref(null)                       // /assistant/settings view
const form = ref({ provider: '', baseUrl: '', model: '', apiKey: '', agentId: '' })
const saving = ref(false)
const saved = ref('')
const models = ref([])
const modelsMsg = ref('')
const loadingModels = ref(false)

// Household default for streaming chat replies (owner-editable; each browser
// overrides it on the chat widget). Default is classic POST.
const chatStreamDefault = ref(false)
const chatSaving = ref(false)
async function loadChatDefault() {
  try { chatStreamDefault.value = !!(await api.get('/assistant/config')).stream } catch (e) { /* keep default */ }
}
async function saveChatDefault(on) {
  chatSaving.value = true
  try {
    chatStreamDefault.value = !!(await api.put('/assistant/chat-settings', { stream: on })).stream
    ui.success('Saved chat default')
  } catch (e) { ui.error(e.message || 'Could not save') } finally { chatSaving.value = false }
}
onMounted(loadChatDefault)

// Async-job AI preference: a provider+model default for background jobs, separate
// from chat. Blank provider = same as chat. A per-run choice still wins.
const jobAi = ref({ enrich: { provider: '', model: '' }, organize: { provider: '', model: '' } })
const jobAiSaving = ref(false)
const JOB_AREAS = [
  { k: 'enrich', l: 'Enrichment (product descriptions)' },
  { k: 'organize', l: 'Background (auto-categorize + families)' },
]
async function loadJobAi() {
  try { jobAi.value = await api.get('/assistant/job-settings') } catch (e) { /* keep defaults */ }
}
async function saveJobAi() {
  jobAiSaving.value = true
  try {
    jobAi.value = await api.put('/assistant/job-settings', jobAi.value)
    ui.success('Saved background-task AI')
  } catch (e) { ui.error(e.message || 'Could not save') } finally { jobAiSaving.value = false }
}
onMounted(loadJobAi)

// ── Cross-app AI settings sync (a copyable, secret-free string) ──────────────
// Edibl / HomeHoard / myMeal ship together; this lets you configure the AI once
// and paste the same provider / model / streaming / background-job choices into
// the others. The API key is NEVER included in the string and NEVER sent on
// apply — each app keeps its own key. Field names are app-agnostic so the same
// string pastes into the other two apps' Settings.
const syncPaste = ref('')
const syncMsg = ref('')
const syncApplying = ref(false)
// Opt-in: include the provider API key in the copied string. Its own field —
// the main provider form's apiKey is write-only and cleared after save.
const syncIncludeKey = ref(false)
const syncKey = ref('')

function b64encode(str) {
  const bytes = new TextEncoder().encode(str)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin)
}
function b64decode(b64) {
  const bin = atob(b64)
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)))
}

function copyAiSettings() {
  const cfg = {
    v: 1,
    provider: form.value.provider || '',
    baseUrl: form.value.baseUrl || '',
    model: form.value.model || '',
    stream: !!chatStreamDefault.value,
    jobEnrich: { provider: jobAi.value.enrich?.provider || '', model: jobAi.value.enrich?.model || '' },
    jobOrganize: { provider: jobAi.value.organize?.provider || '', model: jobAi.value.organize?.model || '' },
  }
  // Only carry the key when the operator opts in AND typed one — and only then
  // bump to the AICFG2 marker so a keyless copy is byte-for-byte the old AICFG1.
  const withKey = syncIncludeKey.value && !!syncKey.value.trim()
  if (withKey) cfg.apiKey = syncKey.value.trim()
  const str = (withKey ? 'AICFG2:' : 'AICFG1:') + b64encode(JSON.stringify(cfg))
  const done = () => { syncKey.value = ''; syncIncludeKey.value = false }
  try {
    navigator.clipboard.writeText(str)
    ui.success(withKey
      ? 'Copied, including your API key — paste into your other apps’ Settings, then clear your clipboard.'
      : 'Copied — paste into your other apps’ Settings. Your API key is not included.')
    done()
  } catch (e) {
    // Clipboard unavailable (e.g. insecure context) — surface it to copy by hand.
    syncPaste.value = str
    ui.info('Copy the string in the box below into your other apps’ Settings.')
    done()
  }
}

async function applyAiSettings() {
  const raw = (syncPaste.value || '').trim()
  syncMsg.value = ''
  const prefix = ['AICFG1:', 'AICFG2:'].find((p) => raw.startsWith(p))
  if (!prefix) {
    syncMsg.value = '⚠️ That doesn’t look like an AI settings string (it should start with “AICFG1:”).'
    return
  }
  // Parsed in one expression so there is no dead initial assignment: the old
  // `let cfg = null` was always overwritten by the try/catch below, which
  // eslint 10's no-useless-assignment (new in its recommended set) flags.
  const cfg = (() => {
    try { return JSON.parse(b64decode(raw.slice(prefix.length))) } catch { return null }
  })()
  if (!cfg || typeof cfg !== 'object') {
    syncMsg.value = '⚠️ Couldn’t read that settings string — it may be incomplete or copied wrong.'
    return
  }
  syncApplying.value = true
  try {
    // Carry the key only when the string included one (AICFG2); a blank/absent
    // apiKey is omitted so the endpoint leaves the stored key unchanged.
    const settings = { provider: cfg.provider || '', baseUrl: cfg.baseUrl || '', model: cfg.model || '' }
    if (typeof cfg.apiKey === 'string' && cfg.apiKey) settings.apiKey = cfg.apiKey
    await api.put('/assistant/settings', settings)
    await api.put('/assistant/chat-settings', { stream: !!cfg.stream })
    await api.put('/assistant/job-settings', {
      enrich: cfg.jobEnrich || { provider: '', model: '' },
      organize: cfg.jobOrganize || { provider: '', model: '' },
    })
    await Promise.all([loadSettings(), loadChatDefault(), loadJobAi()])
    const hadKey = typeof cfg.apiKey === 'string' && !!cfg.apiKey
    syncPaste.value = ''  // don't leave the secret in the DOM
    ui.success(hadKey
      ? 'AI settings and API key applied.'
      : 'AI settings applied. Add this provider’s API key above if it needs one.')
  } catch (e) {
    syncMsg.value = '⚠️ ' + (e.message || 'Could not apply settings.')
  } finally {
    syncApplying.value = false
  }
}

// Model picker for the background-task preference: probe the chosen provider
// (blank = the chat provider) for its models, so you can pick a small/local SLM
// instead of typing it. Uses that provider's saved config.
const jobModels = ref({ enrich: [], organize: [] })
const jobModelsLoading = ref({ enrich: false, organize: false })
async function listJobModels(area) {
  jobModelsLoading.value[area] = true
  try {
    const r = await api.post('/assistant/models', { provider: jobAi.value[area].provider })
    jobModels.value[area] = r.models || []
    if (r.error) ui.error('Could not list models: ' + r.error)
    else if (!jobModels.value[area].length) ui.info('No models found — type the model name')
  } catch (e) { ui.error(e.message || 'Could not list models') } finally { jobModelsLoading.value[area] = false }
}

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
async function useCandidate(cnd) {
  mmForm.value.url = cnd.url
  mmMsg.value = `Connecting “${cnd.name}”…`
  await saveMyMeal()
}
function decodeConnect(str, expectApp) {
  const m = /^([a-z]+)-connect:(.+)$/.exec((str || '').trim())
  if (!m) return null
  try {
    const obj = JSON.parse(decodeURIComponent(escape(atob(m[2]))))
    return (!expectApp || obj.app === expectApp) ? obj : null
  } catch (e) { return null }
}
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

// AI descriptions: an async background job (survives navigation) that looks
// products up online (Ollama web search) and stores searchable text.
const enrichJob = ref(null)
const enrichStarting = ref(false)
let enrichTimer = null
let enrichPollFails = 0
const enrichActive = computed(() =>
  enrichJob.value && ['pending', 'running'].includes(enrichJob.value.status))

async function describeProducts() {
  if (enrichStarting.value) return
  enrichStarting.value = true
  try {
    enrichJob.value = await api.post('/jobs/enrich')
    pollEnrich()
  } catch (e) { ui.error(e.message || 'Could not start enrichment.') }
  finally { enrichStarting.value = false }
}
async function pollEnrich() {
  if (!enrichJob.value) return
  const id = enrichJob.value.id
  try {
    enrichJob.value = await api.get(`/jobs/${id}`)
    enrichPollFails = 0
    if (enrichJob.value.status === 'done') {
      const r = enrichJob.value.result || {}
      ui.success(`Described ${r.described ?? 0} product(s).` +
        (r.remaining ? ` ${r.remaining} still missing — run again to continue.` : ''))
      return
    }
    if (enrichJob.value.status === 'error') {
      ui.error(enrichJob.value.error || 'Enrichment failed.')
      return
    }
  } catch (e) {
    if (++enrichPollFails >= 5) { enrichJob.value = null; ui.error('Lost track of the job.'); return }
  }
  enrichTimer = setTimeout(pollEnrich, 1500)
}
async function resumeEnrich() {
  try {
    const r = await api.get('/jobs?kind=enrich')
    const active = (r.items || []).find(j => ['pending', 'running'].includes(j.status))
    if (active) { enrichJob.value = active; pollEnrich() }
  } catch (e) { /* optional */ }
}
onUnmounted(() => clearTimeout(enrichTimer))

// AI organize: auto-categorize products + propose family groupings (jobs).
const {
  job: catJob, starting: catStarting, active: catActive,
  start: startCategorize, resume: resumeCategorize, stop: stopCategorize,
} = useJobRunner('categorize', {
  onDone: (j) => j.status === 'error'
    ? ui.error(j.error || 'Categorize failed.')
    : ui.success(`Categorize: ${j.result?.applied ?? 0} applied, ${j.result?.queued ?? 0} to review.`),
})
const {
  starting: cluStarting, active: cluActive,
  start: startCluster, resume: resumeCluster, stop: stopCluster,
} = useJobRunner('cluster', {
  onDone: (j) => j.status === 'error'
    ? ui.error(j.error || 'Grouping failed.')
    : ui.success(`Found ${j.result?.proposed ?? 0} grouping(s) to review.`),
})
const organizeForm = ref({ note: '', model: '' })
function organizeBody() {
  const b = {}
  if (organizeForm.value.note.trim()) b.note = organizeForm.value.note.trim()
  if (organizeForm.value.model.trim()) b.model = organizeForm.value.model.trim()
  return b
}
// Update existing data: reprocess items added before the ingestion fixes
// (re-classify stuck "other" items, assign families, recompute estimated expiries).
const {
  job: reproJob, active: reproActive,
  start: startReprocess, resume: resumeReprocess, stop: stopReprocess,
} = useJobRunner('reprocess', {
  onDone: (j) => j.status === 'error'
    ? ui.error(j.error || 'Update failed.')
    : ui.success(`Updated existing data — ${j.result?.reclassified ?? 0} re-categorized · `
        + `${j.result?.familyAssigned ?? 0} grouped · ${j.result?.expiryUpdated ?? 0} expiries fixed`),
})
onMounted(() => { resumeCategorize(); resumeCluster(); resumeReprocess() })
onUnmounted(() => { stopCategorize(); stopCluster(); stopReprocess() })

onMounted(() => { loadSettings(); loadMyMeal(); resumeEnrich() })
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

      <label class="row" style="gap:8px;align-items:center;margin-bottom:12px">
        <input type="checkbox" style="width:auto" :checked="chatStreamDefault" :disabled="chatSaving"
          @change="saveChatDefault($event.target.checked)" />
        <span>Stream chat responses by default
          <span class="muted" style="font-size:.8rem">— show replies as they're written, instead of all at once</span></span>
      </label>

      <label class="field"><span>Provider</span>
        <select v-model="form.provider">
          <option v-for="p in s.providers" :key="p" :value="p">{{ providerLabels[p] || p }}</option>
        </select></label>

      <template v-if="form.provider && form.provider !== 'homeassistant'">
        <label class="field"><span>Base URL</span>
          <input v-model="form.baseUrl" :placeholder="def.baseUrl || ''" @change="canList && loadModels()" /></label>

        <label v-if="showKey" class="field">
          <span>API key {{ needsKey ? '' : '(optional)' }} {{ s.hasKeys?.[form.provider] ? '— saved, leave blank to keep' : '' }}</span>
          <input v-model="form.apiKey" type="password" :placeholder="s.hasKeys?.[form.provider] ? '•••••••••• saved' : (needsKey ? 'sk-…' : 'only if your Ollama needs one')" /></label>

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
    <h2>AI for background tasks</h2>
    <p class="muted" style="margin-top:0">Optionally run background jobs on a different model
      than chat — e.g. a cheap or local model for bulk work. <strong>Same as chat</strong> uses
      the provider above (add a model to just change the model). A per-run choice (on the job
      buttons) still wins. Switching to a hosted provider uses that provider's own saved key —
      set the key by selecting that provider in the chat settings above.</p>
    <div v-for="area in JOB_AREAS" :key="area.k" style="margin-bottom:14px">
      <div class="muted" style="font-size:.85rem;font-weight:600;margin-bottom:4px">{{ area.l }}</div>
      <div class="row" style="gap:8px">
        <select v-model="jobAi[area.k].provider" style="flex:1">
          <option value="">Same as chat</option>
          <option v-for="p in ['ollama','openai','anthropic','homeassistant']" :key="p" :value="p">
            {{ providerLabels[p] || p }}
          </option>
        </select>
        <select v-if="jobModels[area.k].length" v-model="jobAi[area.k].model" style="flex:1">
          <option value="">Default model</option>
          <option v-for="m in jobModels[area.k]" :key="m" :value="m">{{ m }}</option>
          <option v-if="jobAi[area.k].model && !jobModels[area.k].includes(jobAi[area.k].model)"
                  :value="jobAi[area.k].model">{{ jobAi[area.k].model }} (current)</option>
        </select>
        <input v-else v-model="jobAi[area.k].model" placeholder="model (optional)" style="flex:1" />
        <button type="button" class="secondary sm" :disabled="jobModelsLoading[area.k]"
                @click="listJobModels(area.k)">{{ jobModelsLoading[area.k] ? '…' : 'List' }}</button>
      </div>
    </div>
    <button :disabled="jobAiSaving" @click="saveJobAi">{{ jobAiSaving ? 'Saving…' : 'Save' }}</button>
  </div>

  <div class="card">
    <h2>🔗 Sync AI settings to your other apps</h2>
    <p class="muted" style="margin-top:0">Running Edibl, HomeHoard and myMeal together? Copy your AI
      configuration here and paste it into the others so all three use the same provider, model,
      streaming and background-task choices. <strong>Your API key is left out by default</strong> — tick the
      box below to include it, or add each app’s key on its own Settings page.</p>
    <label class="row" style="gap:8px;align-items:center;margin-bottom:6px;font-size:.9rem">
      <input type="checkbox" v-model="syncIncludeKey" />
      <span>Also include my API key in the copied text</span>
    </label>
    <label v-if="syncIncludeKey" class="field" style="margin-bottom:6px">
      <span>API key to include</span>
      <input type="password" v-model="syncKey" autocomplete="off" placeholder="Paste the provider key to copy" /></label>
    <p v-if="syncIncludeKey" class="muted" style="font-size:.8rem;margin:0 0 10px">⚠️ Your API key will be
      embedded in the copied text — treat it like a password and only paste it into your own apps, then
      clear your clipboard.</p>
    <div class="row wrap" style="gap:8px;align-items:center;margin-bottom:12px">
      <button class="secondary" @click="copyAiSettings">📋 Copy AI settings</button>
      <span class="muted" style="font-size:.8rem">{{ syncIncludeKey ? 'Copies your settings and the key above.' : 'Copies everything except the API key.' }}</span>
    </div>
    <label class="field"><span>Paste settings from another app</span>
      <input v-model="syncPaste" placeholder="AICFG1:…" @keyup.enter="applyAiSettings" /></label>
    <div class="row" style="justify-content:flex-end;align-items:center;gap:10px;margin-top:2px">
      <span v-if="syncMsg" class="muted" style="font-size:.85rem">{{ syncMsg }}</span>
      <button :disabled="syncApplying || !syncPaste.trim()" @click="applyAiSettings">{{ syncApplying ? 'Applying…' : 'Apply' }}</button>
    </div>
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

  <div class="card">
    <h2>✨ AI product descriptions</h2>
    <p class="muted" style="margin-top:0">Look products up online (Ollama web search) and store a short searchable
      description, so search finds them by what they actually are. Needs an Ollama search key
      (add-on option <code>ollama_search_key</code> / <code>EDIBL_OLLAMA_SEARCH_KEY</code>).
      Runs in the background — you can leave this page.</p>
    <button v-if="!enrichActive" class="secondary" :disabled="enrichStarting" @click="describeProducts">
      {{ enrichStarting ? 'Starting…' : 'Describe products missing a description' }}</button>
    <div v-else style="max-width:420px">
      <div class="muted" style="font-size:0.85rem;margin-bottom:6px">
        Describing… {{ enrichJob.done }}<span v-if="enrichJob.total">/{{ enrichJob.total }}</span> products</div>
      <progress :value="enrichJob.done" :max="enrichJob.total || 1" style="width:100%"></progress>
    </div>
  </div>

  <div class="card">
    <h2>AI organize</h2>
    <p class="muted" style="margin-top:0">Auto-categorize products and propose display
      families with your AI provider. Confident categories are applied automatically;
      the rest wait for your review, and your accept/reject choices teach later runs.</p>
    <div style="display:flex;gap:8px;max-width:520px;margin-bottom:10px">
      <label style="flex:2">
        <span class="muted" style="font-size:0.85rem">Note (optional guidance)</span>
        <input v-model="organizeForm.note" placeholder="e.g. treat spices as one family" style="width:100%;margin-top:4px" />
      </label>
      <label style="flex:1">
        <span class="muted" style="font-size:0.85rem">Model (optional)</span>
        <input v-model="organizeForm.model" placeholder="override model" style="width:100%;margin-top:4px" />
      </label>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button class="secondary" :disabled="catStarting || catActive" @click="startCategorize(organizeBody())">
        {{ catActive ? `Categorizing… ${catJob.done}/${catJob.total || '…'}` : 'Auto-categorize products' }}
      </button>
      <button class="secondary" :disabled="cluStarting || cluActive" @click="startCluster(organizeBody())">
        {{ cluActive ? 'Finding families…' : 'Propose families' }}
      </button>
      <router-link to="/review" class="muted" style="font-size:.9rem">Review suggestions →</router-link>
    </div>
  </div>

  <div class="card">
    <h2>Update existing data</h2>
    <p class="muted" style="margin-top:0">Reprocess items you added earlier with the current
      logic — re-classify anything stuck in “other”, group them into families, and fix estimated
      expiries. Categories and dates you set yourself are never changed. Safe to run again.</p>
    <button class="secondary" :disabled="reproActive" @click="startReprocess()">
      {{ reproActive ? `Updating… ${reproJob.done}/${reproJob.total || '…'}` : 'Update existing data' }}
    </button>
    <progress v-if="reproActive" :value="reproJob.done" :max="reproJob.total || 1"
              style="width:100%;max-width:520px;margin-top:8px"></progress>
  </div>
</template>

<style scoped>
/* Mimic myMeal's Settings: calm single-column forms — field controls sit at a
   readable width instead of stretching the whole card — and non-muted labels. */
.card .field input:not([type="checkbox"]):not([type="file"]),
.card .field select,
.card .field textarea {
  max-width: 520px;
}
.card label.field > span { color: var(--text); font-size: .84rem; }
</style>
