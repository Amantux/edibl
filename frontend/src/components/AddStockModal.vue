<script setup>
// Self-contained "Add stock" dialog, usable from anywhere (Stock page, Dashboard,
// omnibox). Fetches its own reference data on open, classifies + autocompletes as
// you type, defaults a repeat item to where most of its kind already lives, supports
// barcode scan, and emits `added` after each add so the host can refresh.
import { ref, computed, nextTick, watch } from 'vue'
import { api } from '../api'
import { ui } from '../ui'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  initialName: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'added'])

const locations = ref([])
const products = ref([])
const suggest = ref({ categories: [], units: [], families: [], storageMethods: [] })
const freshnessScales = ref({})
const groups = ref([])
const showMore = ref(false)
const nameInput = ref(null)
const loaded = ref(false)

function blankForm() {
  return { productName: '', category: '', family: '', quantity: 1, unit: 'count',
    storageMethod: 'refrigerated', freshness: '', locationId: '', source: '',
    barcode: '', bestBy: '', itemType: 'food' }
}
const form = ref(blankForm())

const productMap = computed(() => {
  const m = {}
  for (const p of products.value) m[(p.name || '').trim().toLowerCase()] = p
  return m
})
// product name → the location holding most of its active lots ("with the others").
const productLocationMode = computed(() => {
  const counts = {}
  for (const g of groups.value) for (const s of (g.lots || [])) {
    const name = (s.product?.name || '').trim().toLowerCase()
    const loc = s.location?.id
    if (!name || !loc) continue
    ;(counts[name] = counts[name] || {})[loc] = (counts[name]?.[loc] || 0) + 1
  }
  const mode = {}
  for (const [name, locs] of Object.entries(counts)) {
    mode[name] = Object.entries(locs).sort((a, b) => b[1] - a[1])[0][0]
  }
  return mode
})
const _SCALE_BY_CAT = { produce: 'produce', bakery: 'bakery', meat: 'meat', seafood: 'meat' }
const conditionScale = computed(() => {
  const cat = (form.value.category || '').trim().toLowerCase()
  const s = freshnessScales.value
  return s[_SCALE_BY_CAT[cat] || 'default'] || s.default || []
})

async function loadData() {
  const [locs, prods, meta, grp, sug] = await Promise.all([
    api.get('/locations').catch(() => []),
    api.get('/products').catch(() => []),
    api.get('/meta').catch(() => ({})),
    api.get('/stock/grouped').catch(() => ({ groups: [] })),
    api.get('/products/suggestions').catch(() => ({})),
  ])
  locations.value = locs
  products.value = prods.items || prods
  freshnessScales.value = meta.freshnessScales || {}
  groups.value = grp.groups || []
  suggest.value = (sug && sug.storageMethods?.length) ? sug : {
    categories: meta.categories || [], units: meta.units || [], families: [],
    storageMethods: meta.storageMethods || [] }
  loaded.value = true
}

// server-side item-name autocomplete (debounced)
const nameOptions = ref([])
let nameTimer = null
function onNameInput() {
  clearTimeout(nameTimer)
  const q = (form.value.productName || '').trim()
  if (q.length < 1) { nameOptions.value = []; return }
  nameTimer = setTimeout(async () => {
    try { nameOptions.value = (await api.get('/products/autocomplete?q=' + encodeURIComponent(q))).names || [] }
    catch (e) { /* keep last */ }
  }, 180)
}

function applyProductDefaults() {
  const key = (form.value.productName || '').trim().toLowerCase()
  const p = productMap.value[key]
  if (p) {
    if (!form.value.category) form.value.category = p.category || ''
    if (!form.value.family) form.value.family = p.family || ''
    if ((!form.value.unit || form.value.unit === 'count') && p.defaultUnit) form.value.unit = p.defaultUnit
  }
  if (!form.value.locationId && productLocationMode.value[key]) {
    form.value.locationId = productLocationMode.value[key]
  }
}

const classifying = ref(false)
const classifyHint = ref('')
let classifyToken = 0
async function classifyAndFill() {
  const name = (form.value.productName || '').trim()
  classifyHint.value = ''
  if (name.length < 2 || productMap.value[name.toLowerCase()]) return
  const token = ++classifyToken
  classifying.value = true
  try {
    const c = await api.post('/stock/classify', { name })
    if (token !== classifyToken) return
    if (!form.value.category && c.category) form.value.category = c.category
    if (!form.value.family && c.family) form.value.family = c.family
    if ((!form.value.unit || form.value.unit === 'count') && c.unit) form.value.unit = c.unit
    if (form.value.storageMethod === 'refrigerated' && c.storageMethod) form.value.storageMethod = c.storageMethod
    form.value.itemType = c.itemType || 'food'
    classifyHint.value = c.category
      ? `✨ ${c.category}${c.storageMethod ? ' · ' + c.storageMethod.replace('_', ' ') : ''}` : ''
  } catch (e) { /* silent */ } finally { if (token === classifyToken) classifying.value = false }
}
function onNameChange() { applyProductDefaults(); classifyAndFill() }

function close() { stopScan(); emit('update:modelValue', false) }

async function submitAdd(keepOpen) {
  if (!form.value.productName.trim()) return
  const body = { ...form.value }
  for (const k of ['bestBy', 'locationId', 'barcode', 'freshness', 'family', 'source', 'category']) {
    if (!body[k]) delete body[k]
  }
  try {
    await api.post('/stock', body)
  } catch (e) { ui.error(e.message || 'Could not add the item.'); return }
  classifyHint.value = ''
  emit('added')
  if (keepOpen) {
    Object.assign(form.value, { productName: '', quantity: 1, barcode: '',
      bestBy: '', freshness: '', itemType: 'food' })
    ui.success('Added — keep going.')
    api.get('/products').then((p) => { products.value = p.items || p })  // learn the new product
    await nextTick(); nameInput.value?.focus()
  } else {
    close()
  }
}
const add = () => submitAdd(false)
const addAnother = () => submitAdd(true)

// barcode
const scanning = ref(false)
const scanVideo = ref(null)
let scanStream = null
async function lookupBarcode() {
  const code = (form.value.barcode || '').trim()
  if (!code) return
  try {
    const res = await api.get('/products/barcode/' + encodeURIComponent(code))
    const hit = res.found ? res.product : res.suggestion
    if (hit) {
      form.value.productName = form.value.productName || hit.name || ''
      if (hit.category) form.value.category = hit.category
      if (hit.family) form.value.family = hit.family
      applyProductDefaults()
      ui.info(res.found ? `Known: ${hit.name}` : `Found “${hit.name}” — check details.`)
    } else ui.info('Barcode not recognized — fill it in and it’ll be remembered.')
  } catch (e) { /* offline optional */ }
}
const canScan = typeof window !== 'undefined' && 'BarcodeDetector' in window
async function startScan() {
  if (!canScan) { ui.info('Camera scan unsupported here — type the number.'); return }
  scanning.value = true
  try {
    scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
    await new Promise((r) => setTimeout(r, 50))
    if (scanVideo.value) { scanVideo.value.srcObject = scanStream; await scanVideo.value.play() }
    const detector = new window.BarcodeDetector()
    const tick = async () => {
      if (!scanning.value) return
      try {
        const codes = await detector.detect(scanVideo.value)
        if (codes && codes.length) { form.value.barcode = codes[0].rawValue; stopScan(); await lookupBarcode(); return }
      } catch (e) { /* frame not ready */ }
      requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  } catch (e) { scanning.value = false; ui.error('Camera error: ' + e.message) }
}
function stopScan() { scanning.value = false; if (scanStream) { scanStream.getTracks().forEach((t) => t.stop()); scanStream = null } }

// Open behaviour: (re)load data, reset the form, prefill + classify a supplied name.
watch(() => props.modelValue, async (v) => {
  if (!v) return
  showMore.value = false
  form.value = blankForm()
  classifyHint.value = ''
  if (!loaded.value) await loadData(); else loadData()  // first open awaits; later refresh in bg
  if (props.initialName) { form.value.productName = props.initialName; onNameChange() }
  await nextTick(); nameInput.value?.focus()
}, { immediate: true })
</script>

<template>
  <div v-if="modelValue" class="modal-backdrop" @click.self="close">
    <div class="card modal" v-trap="close" aria-label="Add stock">
      <h2>Add stock</h2>
      <label class="field" style="margin-bottom:6px"><span>What is it?</span>
        <input ref="nameInput" v-model="form.productName" list="dl-add-names" placeholder="e.g. Organic milk"
          @input="onNameInput" @change="onNameChange" @keyup.enter="add" /></label>
      <p class="muted" style="font-size:.75rem;margin:0 0 12px;min-height:1.1em">
        <span v-if="classifying">✨ classifying…</span>
        <span v-else-if="classifyHint">{{ classifyHint }} — auto-filled, edit anything below.</span></p>
      <div class="row">
        <label class="field" style="width:110px"><span>Quantity</span>
          <input type="number" min="0" v-model.number="form.quantity" @keyup.enter="add" /></label>
        <label class="field" style="width:120px"><span>Unit</span>
          <input v-model="form.unit" list="dl-add-units" /></label>
        <label class="field" style="flex:1"><span>Location</span>
          <select v-model="form.locationId"><option value="">Unassigned</option>
            <option v-for="l in locations" :key="l.id" :value="l.id">{{ l.name }}</option></select></label>
      </div>
      <label class="field" v-if="conditionScale.length"><span>Condition (optional)</span>
        <select v-model="form.freshness">
          <option value="">Not tracked</option>
          <option v-for="f in conditionScale" :key="f.key" :value="f.key">{{ f.level }} · {{ f.label }}</option>
        </select></label>

      <button type="button" class="ghost sm morebtn" @click="showMore = !showMore">
        {{ showMore ? '▾ Fewer options' : '▸ More options — category, storage, expiry, barcode…' }}</button>

      <template v-if="showMore">
        <div class="row">
          <label class="field" style="flex:1"><span>Category</span>
            <input v-model="form.category" list="dl-add-cats" placeholder="e.g. dairy" /></label>
          <label class="field" style="flex:1"><span>Group (shows together, e.g. Milk)</span>
            <input v-model="form.family" list="dl-add-fam" placeholder="optional" /></label>
        </div>
        <div class="row">
          <label class="field" style="flex:1"><span>Storage</span>
            <input v-model="form.storageMethod" list="dl-add-storage" /></label>
        </div>
        <div class="row">
          <label class="field" style="flex:1"><span>Source (where from)</span>
            <input v-model="form.source" placeholder="e.g. Costco, farm share" /></label>
          <label class="field" style="flex:1"><span>Best-by date (blank = estimate)</span>
            <input type="date" v-model="form.bestBy" /></label>
        </div>
        <div class="row">
          <label class="field" style="flex:1"><span>Barcode</span>
            <input v-model="form.barcode" placeholder="scan or type" @keyup.enter="lookupBarcode" @blur="lookupBarcode" /></label>
          <button type="button" class="secondary" style="align-self:flex-end;height:38px" @click="startScan">📷 Scan</button>
        </div>
        <div v-if="scanning" class="scanbox"><video ref="scanVideo" muted playsinline></video>
          <button type="button" class="ghost sm" @click="stopScan">Stop</button></div>
      </template>

      <p class="muted" style="font-size:.8rem;margin-top:4px">Leave expiry blank — Edibl estimates it and learns from your history.</p>
      <div class="row" style="justify-content:flex-end;margin-top:6px;gap:8px">
        <button class="secondary" @click="close">Cancel</button>
        <button class="secondary" :disabled="!form.productName.trim()" @click="addAnother">Add &amp; another</button>
        <button :disabled="!form.productName.trim()" @click="add">Add</button></div>

      <datalist id="dl-add-names"><option v-for="n in nameOptions" :key="n" :value="n" /></datalist>
      <datalist id="dl-add-cats"><option v-for="c in suggest.categories" :key="c" :value="c" /></datalist>
      <datalist id="dl-add-units"><option v-for="u in suggest.units" :key="u" :value="u" /></datalist>
      <datalist id="dl-add-fam"><option v-for="f in suggest.families" :key="f" :value="f" /></datalist>
      <datalist id="dl-add-storage"><option v-for="s in suggest.storageMethods" :key="s" :value="s" /></datalist>
    </div>
  </div>
</template>

<style scoped>
.morebtn { padding-left: 0; }
.scanbox { margin-top: 10px; }
.scanbox video { width: 100%; border-radius: var(--radius-sm); background: #000; max-height: 240px; }
</style>
