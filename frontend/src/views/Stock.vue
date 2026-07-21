<script setup>
import { ref, computed, reactive, onMounted, nextTick } from 'vue'
import { api } from '../api'

const groups = ref([])            // grouped view (all stock)
const flatItems = ref([])         // freezer / wine views
const suggest = ref({ categories: [], units: [], families: [], freshness: [], storageMethods: [], names: [] })
const products = ref([])          // known products -> smart prefill on name match
const showMore = ref(false)       // progressive disclosure in the Add modal
const nameInput = ref(null)
const locations = ref([])
const loading = ref(true)
const filter = ref({ view: 'all' })
const expanded = reactive({})
const showAdd = ref(false)
const showBulk = ref(false)
const assistantCfg = ref({ enabled: false })
const receiptText = ref('')
const extracting = ref(false)
const consumeFor = ref(null)
const consumeQty = ref(null)
const toast = ref('')
const form = ref(blankForm())
const bulk = ref(blankBulk())

// barcode
const scanning = ref(false)
const scanVideo = ref(null)
let scanStream = null

function blankForm() {
  return { productName: '', category: '', family: '', quantity: 1, unit: 'count',
    storageMethod: 'refrigerated', freshness: '', locationId: '', source: '',
    barcode: '', expiryDate: '' }
}
function blankBulk() {
  return { shared: { storageMethod: 'refrigerated', category: '', family: '', locationId: '', source: '' },
    rows: [{ name: '', quantity: 1, unit: 'count', storageMethod: '' }] }
}

async function loadSuggest() {
  try { suggest.value = await api.get('/products/suggestions') } catch (e) { /* seeds fallback below */ }
  if (!suggest.value.storageMethods?.length) {
    const m = await api.get('/meta')
    suggest.value = { categories: m.categories, units: m.units, families: [],
      freshness: m.freshnessLevels || m.lifecycleStates || [], storageMethods: m.storageMethods, names: [] }
  }
}

async function load() {
  loading.value = true
  if (filter.value.view === 'all') {
    groups.value = (await api.get('/stock/grouped')).groups
  } else {
    const path = filter.value.view === 'freezer' ? '/dashboard/freezer' : '/dashboard/wine'
    flatItems.value = (await api.get(path)).items
  }
  loading.value = false
}
const productMap = computed(() => {
  const m = {}
  for (const p of products.value) m[(p.name || '').trim().toLowerCase()] = p
  return m
})
async function loadProducts() { try { products.value = await api.get('/products') } catch (e) { /* optional */ } }
async function openAdd() {
  showAdd.value = true; showMore.value = false
  await nextTick(); nameInput.value?.focus()
}
// Fill in what we already know about a named product (category/group/unit), so a
// repeat item needs only a name + quantity. Only fills blanks — never clobbers.
function applyProductDefaults() {
  const p = productMap.value[(form.value.productName || '').trim().toLowerCase()]
  if (!p) return
  if (!form.value.category) form.value.category = p.category || ''
  if (!form.value.family) form.value.family = p.family || ''
  if ((!form.value.unit || form.value.unit === 'count') && p.defaultUnit) form.value.unit = p.defaultUnit
}

onMounted(async () => {
  locations.value = await api.get('/locations')
  await loadSuggest()
  await loadProducts()
  try { assistantCfg.value = await api.get('/assistant/config') } catch (e) { /* optional */ }
  await load()
})

function applyExtracted(res, note) {
  if (res.error) { flash(res.error); return }
  if (!res.items?.length) { flash('No items found — check the input or add rows manually.'); return }
  bulk.value.rows = res.items.map((i) => ({
    name: i.name, quantity: i.quantity ?? 1, unit: i.unit || 'count',
    category: i.category || '', storageMethod: '' }))
  flash(`Extracted ${res.items.length} items ${note} — review and Add all.`)
}
async function extractReceipt() {
  if (!receiptText.value.trim()) return
  if (!assistantCfg.value.enabled) { flash('Set an LLM provider in the add-on options to extract receipts.'); return }
  extracting.value = true
  try { applyExtracted(await api.post('/stock/extract', { text: receiptText.value }), 'from text') }
  catch (e) { flash('Extract failed: ' + (e.message || 'error')) } finally { extracting.value = false }
}
function downscale(file, max, quality) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height))
      const w = Math.round(img.width * scale), h = Math.round(img.height * scale)
      const canvas = document.createElement('canvas'); canvas.width = w; canvas.height = h
      canvas.getContext('2d').drawImage(img, 0, 0, w, h)
      resolve(canvas.toDataURL('image/jpeg', quality).split(',')[1])
    }
    img.onerror = reject
    img.src = URL.createObjectURL(file)
  })
}
async function extractPhoto(e) {
  const file = e.target.files?.[0]; e.target.value = ''
  if (!file) return
  if (!assistantCfg.value.enabled) { flash('Set a provider with a vision model (gpt-4o / claude / llava) to read photos.'); return }
  extracting.value = true
  try {
    const b64 = await downscale(file, 1600, 0.8)
    applyExtracted(await api.post('/stock/extract', { image: b64, mediaType: 'image/jpeg' }), 'from photo')
  } catch (err) { flash('Photo extract failed: ' + (err.message || 'error')) } finally { extracting.value = false }
}

function toggle(key) { expanded[key] = !expanded[key] }
function flash(msg) { if (!msg) return; toast.value = msg; setTimeout(() => (toast.value = ''), 6000) }
function expLabel(s) { return s.daysToExpiry == null ? '—' : (s.daysToExpiry < 0 ? 'expired' : s.daysToExpiry + 'd') }
function nextExp(g) {
  if (!g.nextExpiry) return '—'
  const d = Math.round((new Date(g.nextExpiry) - Date.now()) / 86400000)
  return d < 0 ? 'expired' : d + 'd'
}

async function submitAdd(keepOpen) {
  if (!form.value.productName.trim()) return
  const body = { ...form.value }
  for (const k of ['expiryDate', 'locationId', 'barcode', 'freshness', 'family', 'source', 'category']) {
    if (!body[k]) delete body[k]
  }
  await api.post('/stock', body)
  if (keepOpen) {
    // Keep the "haul" context (location / storage / category / group / unit /
    // source); reset only the item-specific fields, and refocus for the next one.
    Object.assign(form.value, { productName: '', quantity: 1, barcode: '',
      expiryDate: '', freshness: '' })
    flash('Added — keep going.')
    refresh()  // background; don't block the next entry
    await nextTick(); nameInput.value?.focus()
  } else {
    showAdd.value = false; form.value = blankForm(); await refresh()
  }
}
const add = () => submitAdd(false)
const addAnother = () => submitAdd(true)
async function refresh() { await loadSuggest(); await loadProducts(); await load() }

// barcode lookup / scan
async function lookupBarcode() {
  const code = form.value.barcode.trim()
  if (!code) return
  try {
    const res = await api.get('/products/barcode/' + encodeURIComponent(code))
    const hit = res.found ? res.product : res.suggestion
    if (hit) {
      form.value.productName = form.value.productName || hit.name || ''
      if (hit.category) form.value.category = hit.category
      if (hit.family) form.value.family = hit.family
      applyProductDefaults()
      flash(res.found ? `Known: ${hit.name}` : `Found “${hit.name}” — check details.`)
    } else flash('Barcode not recognized — fill it in and it’ll be remembered.')
  } catch (e) { /* offline optional */ }
}
const canScan = typeof window !== 'undefined' && 'BarcodeDetector' in window
async function startScan() {
  if (!canScan) { flash('Camera scan unsupported here — type the number.'); return }
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
  } catch (e) { scanning.value = false; flash('Camera error: ' + e.message) }
}
function stopScan() { scanning.value = false; if (scanStream) { scanStream.getTracks().forEach((t) => t.stop()); scanStream = null } }

// bulk
function addBulkRow() { bulk.value.rows.push({ name: '', quantity: 1, unit: 'count', storageMethod: '' }) }
function pasteBulk(text) {
  const rows = text.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
    const [name, qty, unit] = l.split(',').map((x) => (x || '').trim())
    return { name, quantity: Number(qty) || 1, unit: unit || 'count', storageMethod: '' }
  })
  if (rows.length) bulk.value.rows = rows
}
async function submitBulk() {
  const rows = bulk.value.rows.filter((r) => r.name.trim())
  if (!rows.length) return
  const shared = { ...bulk.value.shared }
  for (const k of ['locationId', 'category', 'family', 'source']) if (!shared[k]) delete shared[k]
  const res = await api.post('/stock/bulk', { shared, items: rows })
  showBulk.value = false; bulk.value = blankBulk(); receiptText.value = ''
  flash(`Added ${res.created} items.`); await refresh()
}

// consume with outcome + freshness
function openConsume(s) { consumeFor.value = s; consumeQty.value = s.quantity }
async function doConsume(outcome) {
  const name = consumeFor.value.product?.name || 'item'
  const res = await api.post(`/stock/${consumeFor.value.id}/consume`, { quantity: consumeQty.value, outcome })
  consumeFor.value = null
  if (res.insight) flash(res.insight)
  // Offer a one-tap Undo — reverses the exact ledger event, not a guess.
  if (res.eventId) lastUndo.value = { eventId: res.eventId, label: `Used ${name}` }
  await refresh()
}
// Open a package (orthogonal to using it) — turns a sealed carton into an open one.
async function openPkg(s) { await api.post(`/stock/${s.id}/open`); await refresh() }
const lastUndo = ref(null)
async function undoLast() {
  if (!lastUndo.value) return
  await api.post(`/inventory/events/${lastUndo.value.eventId}/reverse`)
  lastUndo.value = null
  await refresh()
}
async function setFresh(s, freshness) { await api.put('/stock/' + s.id, { freshness }); await load() }
async function del(s) {
  if (!confirm(`Remove ${s.product?.name}?`)) return
  await api.del('/stock/' + s.id); await refresh()
}

const title = computed(() => filter.value.view === 'freezer' ? '❄️ Freezer & long-term' :
  filter.value.view === 'wine' ? '🍷 Wine & cellar' : 'Stock')
const count = computed(() => filter.value.view === 'all' ? groups.value.length : flatItems.value.length)
</script>

<template>
  <div class="page-head"><h1>{{ title }}</h1><span class="badge">{{ count }}</span>
    <div class="grow"></div>
    <button class="secondary" @click="showBulk = true">⧉ Bulk add</button>
    <button @click="openAdd">＋ Add stock</button></div>

  <div v-if="toast" class="toast">{{ toast }}</div>
  <div v-if="lastUndo" class="toast">{{ lastUndo.label }}
    · <button class="ghost sm" @click="undoLast">Undo</button>
    · <button class="ghost sm" @click="lastUndo = null">Dismiss</button></div>

  <div class="toolbar">
    <select v-model="filter.view" style="width:auto" @change="load">
      <option value="all">All stock (grouped)</option>
      <option value="freezer">Freezer / vacuum-sealed</option>
      <option value="wine">Wine & alcohol</option>
    </select>
  </div>

  <div v-if="loading" class="muted">Loading…</div>

  <!-- Grouped view -->
  <div v-else-if="filter.view==='all' && groups.length" class="card tablewrap" style="padding:0">
    <table>
      <thead><tr><th>Group</th><th>Products</th><th>On hand</th><th>Next expiry</th><th></th></tr></thead>
      <tbody>
        <template v-for="g in groups" :key="g.group">
          <tr class="grp" @click="toggle(g.group)">
            <td><span class="caret">{{ expanded[g.group] ? '▾' : '▸' }}</span>
              <strong>{{ g.group }}</strong> <span v-if="g.category" class="chip">{{ g.category }}</span></td>
            <td class="muted">{{ g.products.join(', ') }}
              <span v-if="g.productCount > 1">· {{ g.productCount }} kinds</span></td>
            <td>{{ g.totalQuantity }} {{ g.unit }} <span class="muted">· {{ g.lotCount }} lot{{ g.lotCount>1?'s':'' }}</span>
              <div v-if="g.summary" class="muted" style="font-size:.7rem">{{ g.summary }}</div></td>
            <td><span class="badge" :class="g.nextExpiryStatus">{{ nextExp(g) }}</span>
              <span v-if="g.expiring || g.expired" class="muted" style="font-size:.7rem">
                {{ g.expired ? g.expired + ' expired' : g.expiring + ' soon' }}</span></td>
            <td></td>
          </tr>
          <tr v-for="s in (expanded[g.group] ? g.lots : [])" :key="s.id" class="lot">
            <td><span class="ind">↳</span> {{ s.product?.name }}
              <span v-if="s.freshness" class="chip">{{ s.freshness }}</span>
              <span v-if="s.attrs?.cut" class="muted"> · {{ s.attrs.animal }} {{ s.attrs.cut }}</span></td>
            <td class="muted">{{ s.location?.name || '—' }}<span v-if="s.source" class="muted"> · {{ s.source }}</span><span v-if="s.addedBy" class="muted"> · 👤 {{ s.addedBy }}</span></td>
            <td>{{ s.quantityKind === 'exact' ? (s.quantity + ' ' + s.unit) : s.quantityText }}
              <span class="chip">{{ s.storageMethod.replace('_',' ') }}</span>
              <span v-if="s.packageState === 'opened'" class="chip">open</span></td>
            <td><span class="badge" :class="s.expiryStatus">{{ expLabel(s) }}</span>
              <span v-if="s.expiryEstimated" class="muted" style="font-size:.7rem"> est</span></td>
            <td style="text-align:right;white-space:nowrap">
              <button v-if="s.packageState === 'sealed'" class="ghost sm" @click="openPkg(s)">Open</button>
              <button class="secondary sm" @click="openConsume(s)">Use</button>
              <button class="ghost sm" @click="del(s)">✕</button></td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>

  <!-- Flat view (freezer / wine) -->
  <div v-else-if="filter.view!=='all' && flatItems.length" class="card tablewrap" style="padding:0">
    <table>
      <thead><tr><th>Item</th><th>Where</th><th>Qty</th><th>Storage</th><th>Expiry</th><th></th></tr></thead>
      <tbody>
        <tr v-for="s in flatItems" :key="s.id">
          <td><strong>{{ s.product?.name }}</strong>
            <span v-if="s.attrs?.cut" class="muted"> · {{ s.attrs.animal }} {{ s.attrs.cut }}</span></td>
          <td class="muted">{{ s.location?.name || '—' }}<span v-if="s.addedBy" class="muted"> · 👤 {{ s.addedBy }}</span></td>
          <td>{{ s.quantityKind === 'exact' ? (s.quantity + ' ' + s.unit) : s.quantityText }}</td>
          <td><span class="chip">{{ s.storageMethod.replace('_',' ') }}</span>
            <span v-if="s.packageState === 'opened'" class="chip">open</span></td>
          <td><span class="badge" :class="s.expiryStatus">{{ expLabel(s) }}</span></td>
          <td style="text-align:right;white-space:nowrap">
            <button class="secondary sm" @click="openConsume(s)">Use</button>
            <button class="ghost sm" @click="del(s)">✕</button></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div v-else class="empty"><div class="ico">🥫</div><p>No stock here yet.</p>
    <button style="margin-top:10px" @click="openAdd">Add your first item</button></div>

  <datalist id="dl-names"><option v-for="n in suggest.names" :key="n" :value="n" /></datalist>
  <datalist id="dl-cats"><option v-for="c in suggest.categories" :key="c" :value="c" /></datalist>
  <datalist id="dl-units"><option v-for="u in suggest.units" :key="u" :value="u" /></datalist>
  <datalist id="dl-fam"><option v-for="f in suggest.families" :key="f" :value="f" /></datalist>
  <datalist id="dl-fresh"><option v-for="f in suggest.freshness" :key="f" :value="f" /></datalist>
  <datalist id="dl-storage"><option v-for="s in suggest.storageMethods" :key="s" :value="s" /></datalist>

  <!-- Add stock -->
  <div v-if="showAdd" class="modal-backdrop" @click.self="showAdd = false; stopScan()">
    <div class="card modal">
      <h2>Add stock</h2>
      <label class="field"><span>What is it?</span>
        <input ref="nameInput" v-model="form.productName" list="dl-names" placeholder="e.g. Organic milk"
          @change="applyProductDefaults" @keyup.enter="add" /></label>
      <div class="row">
        <label class="field" style="width:110px"><span>Quantity</span>
          <input type="number" min="0" v-model.number="form.quantity" @keyup.enter="add" /></label>
        <label class="field" style="width:120px"><span>Unit</span>
          <input v-model="form.unit" list="dl-units" /></label>
        <label class="field" style="flex:1"><span>Location</span>
          <select v-model="form.locationId"><option value="">Unassigned</option>
            <option v-for="l in locations" :key="l.id" :value="l.id">{{ l.name }}</option></select></label>
      </div>

      <button type="button" class="ghost sm morebtn" @click="showMore = !showMore">
        {{ showMore ? '▾ Fewer options' : '▸ More options — category, storage, expiry, barcode…' }}</button>

      <template v-if="showMore">
        <div class="row">
          <label class="field" style="flex:1"><span>Category</span>
            <input v-model="form.category" list="dl-cats" placeholder="e.g. dairy" /></label>
          <label class="field" style="flex:1"><span>Group (shows together, e.g. Milk)</span>
            <input v-model="form.family" list="dl-fam" placeholder="optional" /></label>
        </div>
        <div class="row">
          <label class="field" style="flex:1"><span>Storage</span>
            <input v-model="form.storageMethod" list="dl-storage" /></label>
          <label class="field" style="width:150px"><span>Freshness</span>
            <input v-model="form.freshness" list="dl-fresh" placeholder="optional" /></label>
        </div>
        <div class="row">
          <label class="field" style="flex:1"><span>Source (where from)</span>
            <input v-model="form.source" placeholder="e.g. Costco, farm share" /></label>
          <label class="field" style="flex:1"><span>Expiry (blank = estimate)</span>
            <input type="date" v-model="form.expiryDate" /></label>
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
        <button class="secondary" @click="showAdd=false; stopScan()">Cancel</button>
        <button class="secondary" :disabled="!form.productName.trim()" @click="addAnother">Add &amp; another</button>
        <button :disabled="!form.productName.trim()" @click="add">Add</button></div>
    </div>
  </div>

  <!-- Bulk add -->
  <div v-if="showBulk" class="modal-backdrop" @click.self="showBulk = false">
    <div class="card modal">
      <h2>⧉ Bulk add</h2>
      <p class="muted" style="margin-top:0">Many items at once — a grocery haul, a farm box, or a butchered animal. Shared settings apply to every row.</p>

      <div class="receipt">
        <label class="field"><span>✨ Paste a receipt / order — or snap a photo</span>
          <textarea v-model="receiptText" rows="3"
            :placeholder="assistantCfg.enabled ? 'Paste your grocery receipt or order confirmation…' : 'Set an LLM provider (add-on options) to auto-extract'" /></label>
        <div class="row" style="justify-content:space-between;align-items:center">
          <label class="secondary sm" :class="{ disabled: extracting || !assistantCfg.enabled }" style="cursor:pointer">📷 Photo
            <input type="file" hidden accept="image/*" capture="environment"
              :disabled="extracting || !assistantCfg.enabled" @change="extractPhoto" /></label>
          <button class="secondary sm" :disabled="extracting || !assistantCfg.enabled || !receiptText.trim()"
            @click="extractReceipt">{{ extracting ? 'Extracting…' : '✨ Extract from text' }}</button>
        </div>
      </div>
      <div class="divider"></div>

      <div class="row">
        <label class="field" style="flex:1"><span>Default storage</span>
          <input v-model="bulk.shared.storageMethod" list="dl-storage" /></label>
        <label class="field" style="flex:1"><span>Default category</span>
          <input v-model="bulk.shared.category" list="dl-cats" /></label>
      </div>
      <div class="row">
        <label class="field" style="flex:1"><span>Group</span><input v-model="bulk.shared.family" list="dl-fam" placeholder="optional" /></label>
        <label class="field" style="flex:1"><span>Source</span><input v-model="bulk.shared.source" placeholder="e.g. Costco" /></label>
        <label class="field" style="flex:1"><span>Location</span>
          <select v-model="bulk.shared.locationId"><option value="">Unassigned</option>
            <option v-for="l in locations" :key="l.id" :value="l.id">{{ l.name }}</option></select></label>
      </div>
      <div class="divider"></div>
      <div v-for="(r,i) in bulk.rows" :key="i" class="row" style="margin-bottom:8px">
        <input v-model="r.name" list="dl-names" placeholder="Item name" style="flex:2" />
        <input type="number" v-model.number="r.quantity" placeholder="qty" style="width:80px" />
        <input v-model="r.unit" list="dl-units" placeholder="unit" style="width:90px" />
        <input v-model="r.storageMethod" list="dl-storage" placeholder="(default)" style="width:130px" />
      </div>
      <button class="secondary sm" @click="addBulkRow">＋ Another row</button>
      <details style="margin-top:8px"><summary class="muted sm">Or paste “name, qty, unit” lines</summary>
        <textarea rows="4" style="width:100%;margin-top:6px" placeholder="Ribeye, 2, pack&#10;Ground beef, 1, kg" @change="(e)=>pasteBulk(e.target.value)"></textarea>
      </details>
      <div class="row" style="justify-content:flex-end;margin-top:14px">
        <button class="secondary" @click="showBulk=false">Cancel</button>
        <button @click="submitBulk">Add all</button></div>
    </div>
  </div>

  <!-- Consume / outcome -->
  <div v-if="consumeFor" class="modal-backdrop" @click.self="consumeFor = null">
    <div class="card modal" style="max-width:420px">
      <h2>{{ consumeFor.product?.name }}</h2>
      <p class="muted" style="margin-top:0">How did it go? This teaches Edibl how long things really last for you.</p>
      <label class="field" style="width:140px"><span>Quantity</span>
        <input type="number" min="0" :max="consumeFor.quantity" v-model.number="consumeQty" /></label>
      <div class="outcome-grid">
        <button class="secondary" @click="doConsume('eaten')">🍽️ Ate it</button>
        <button class="secondary" @click="doConsume('spoiled')">🦠 Went bad</button>
        <button class="secondary" @click="doConsume('expired')">📅 Expired</button>
        <button class="secondary" @click="doConsume('discarded')">🗑️ Tossed</button>
      </div>
      <div class="divider"></div>
      <label class="field"><span>Or just update freshness</span>
        <input list="dl-fresh" placeholder="fresh / ripe / opened…" @change="(e)=>setFresh(consumeFor, e.target.value)" /></label>
      <div class="row" style="justify-content:flex-end;margin-top:10px">
        <button class="ghost" @click="consumeFor = null">Close</button></div>
    </div>
  </div>
</template>

<style scoped>
.toast { background: rgba(47,158,87,.14); border: 1px solid var(--primary, #2f9e57);
  color: var(--text, #eee); padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; }
.chip { margin-left: 6px; }
.grp { cursor: pointer; }
.grp:hover { background: var(--surface, rgba(255,255,255,.03)); }
.caret { display: inline-block; width: 1em; color: var(--muted, #999); }
.lot td { background: var(--surface, rgba(255,255,255,.02)); font-size: .9rem; }
.ind { color: var(--muted, #999); margin-right: 4px; }
.scanbox { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; margin-bottom: 10px; }
.scanbox video { width: 100%; max-height: 200px; border-radius: 8px; background: #000; }
.outcome-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
.outcome-grid button { width: 100%; }
.sm { font-size: .8rem; }
.receipt textarea { width: 100%; resize: vertical; }
.secondary.disabled { opacity: .5; pointer-events: none; }
.morebtn { padding: 4px 0; margin: 2px 0 6px; color: var(--muted, #999); }
.morebtn:hover { color: var(--text, #eee); }
</style>
