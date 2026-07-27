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
onMounted(() => { resumeCategorize(); resumeCluster() })
onUnmounted(() => { stopCategorize(); stopCluster() })

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
