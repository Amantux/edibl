<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'

const items = ref([])
const meta = ref({ categories: [], units: [], storageMethods: [], lifecycleStates: [], outcomes: [] })
const locations = ref([])
const loading = ref(true)
const filter = ref({ category: '', storageMethod: '', status: '', view: 'all' })
const showAdd = ref(false)
const showBulk = ref(false)
const consumeFor = ref(null)      // lot being resolved
const consumeQty = ref(null)
const toast = ref('')
const form = ref(blankForm())

// bulk-add state
const bulk = ref(blankBulk())

// barcode scanning
const scanning = ref(false)
const scanVideo = ref(null)
let scanStream = null

function blankForm() {
  return { productName: '', category: 'other', quantity: 1, unit: 'count',
    storageMethod: 'refrigerated', state: '', locationId: '', barcode: '',
    purchaseDate: '', expiryDate: '', cost: null }
}
function blankBulk() {
  return { shared: { storageMethod: 'refrigerated', category: 'other', locationId: '', source: '' },
    rows: [{ name: '', quantity: 1, unit: 'count', storageMethod: '' }] }
}

async function load() {
  loading.value = true
  const params = new URLSearchParams()
  if (filter.value.category) params.set('category', filter.value.category)
  if (filter.value.storageMethod) params.set('storageMethod', filter.value.storageMethod)
  if (filter.value.status) params.set('status', filter.value.status)
  let path = '/stock?' + params.toString()
  if (filter.value.view === 'freezer') path = '/dashboard/freezer'
  if (filter.value.view === 'wine') path = '/dashboard/wine'
  const res = await api.get(path)
  items.value = res.items
  loading.value = false
}
onMounted(async () => {
  meta.value = await api.get('/meta')
  locations.value = await api.get('/locations')
  await load()
})

function flash(msg) { if (!msg) return; toast.value = msg; setTimeout(() => (toast.value = ''), 6000) }

async function add() {
  if (!form.value.productName.trim()) return
  const body = { ...form.value }
  for (const k of ['purchaseDate', 'expiryDate', 'locationId', 'barcode', 'state']) {
    if (!body[k]) delete body[k]
  }
  await api.post('/stock', body)
  showAdd.value = false; form.value = blankForm(); await load()
}

// ---- barcode lookup + scan ----
async function lookupBarcode(target) {
  const code = (target === 'form' ? form.value.barcode : '').trim()
  if (!code) return
  try {
    const res = await api.get('/products/barcode/' + encodeURIComponent(code))
    const hit = res.found ? res.product : res.suggestion
    if (hit) {
      form.value.productName = form.value.productName || hit.name || ''
      if (hit.category) form.value.category = hit.category
      flash(res.found ? `Known product: ${hit.name}` : `Found “${hit.name}” — check the details.`)
    } else {
      flash('Barcode not recognized — fill in the details and it’ll be remembered.')
    }
  } catch (e) { /* offline lookup optional */ }
}

const canScan = typeof window !== 'undefined' && 'BarcodeDetector' in window
async function startScan() {
  if (!canScan) { flash('Barcode camera scan isn’t supported in this browser — type the number instead.'); return }
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
        if (codes && codes.length) {
          form.value.barcode = codes[0].rawValue
          stopScan(); await lookupBarcode('form'); return
        }
      } catch (e) { /* frame not ready */ }
      requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  } catch (e) { scanning.value = false; flash('Couldn’t open the camera: ' + e.message) }
}
function stopScan() {
  scanning.value = false
  if (scanStream) { scanStream.getTracks().forEach((t) => t.stop()); scanStream = null }
}

// ---- bulk add ----
function addBulkRow() { bulk.value.rows.push({ name: '', quantity: 1, unit: 'count', storageMethod: '' }) }
function pasteBulk(text) {
  // "name, qty, unit" per line
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
  if (!shared.locationId) delete shared.locationId
  const res = await api.post('/stock/bulk', { shared, items: rows })
  showBulk.value = false; bulk.value = blankBulk()
  flash(`Added ${res.created} items.`); await load()
}

// ---- consume with outcome ----
function openConsume(s) { consumeFor.value = s; consumeQty.value = s.quantity }
async function doConsume(outcome) {
  const s = consumeFor.value
  const res = await api.post(`/stock/${s.id}/consume`, { quantity: consumeQty.value, outcome })
  consumeFor.value = null
  if (res.insight) flash(res.insight)
  await load()
}
async function setState(s, state) { await api.put('/stock/' + s.id, { state }); await load() }

async function del(s) {
  if (!confirm(`Remove ${s.product?.name}?`)) return
  await api.del('/stock/' + s.id); await load()
}

const title = computed(() => filter.value.view === 'freezer' ? '❄️ Freezer & long-term' :
  filter.value.view === 'wine' ? '🍷 Wine & cellar' : 'Stock')
</script>

<template>
  <div class="page-head"><h1>{{ title }}</h1><span class="badge">{{ items.length }}</span>
    <div class="grow"></div>
    <button class="secondary" @click="showBulk = true">⧉ Bulk add</button>
    <button @click="showAdd = true">＋ Add stock</button></div>

  <div v-if="toast" class="toast">{{ toast }}</div>

  <div class="toolbar">
    <select v-model="filter.view" style="width:auto" @change="load">
      <option value="all">All stock</option><option value="freezer">Freezer / vacuum-sealed</option>
      <option value="wine">Wine & alcohol</option>
    </select>
    <select v-if="filter.view==='all'" v-model="filter.category" style="width:auto" @change="load">
      <option value="">All categories</option><option v-for="c in meta.categories" :key="c" :value="c">{{ c }}</option>
    </select>
    <select v-if="filter.view==='all'" v-model="filter.status" style="width:auto" @change="load">
      <option value="">Any freshness</option><option value="fresh">Fresh</option>
      <option value="expiring">Expiring</option><option value="expired">Expired</option>
    </select>
  </div>

  <div v-if="loading" class="muted">Loading…</div>
  <div v-else-if="items.length" class="card" style="padding:0;overflow:hidden">
    <table>
      <thead><tr><th>Item</th><th>Where</th><th>Qty</th><th>Storage</th><th>Expiry</th><th></th></tr></thead>
      <tbody>
        <tr v-for="s in items" :key="s.id">
          <td><strong>{{ s.product?.name }}</strong>
            <span v-if="s.attrs?.cut" class="muted"> · {{ s.attrs.animal }} {{ s.attrs.cut }}</span>
            <span v-if="s.state" class="chip" :title="'ripeness'">{{ s.state }}</span></td>
          <td class="muted">{{ s.location?.name || '—' }}</td>
          <td>{{ s.quantity }} {{ s.unit }}</td>
          <td><span class="chip">{{ s.storageMethod.replace('_',' ') }}</span></td>
          <td><span class="badge" :class="s.expiryStatus">
            {{ s.daysToExpiry == null ? '—' : (s.daysToExpiry < 0 ? 'expired' : s.daysToExpiry+'d') }}</span>
            <span v-if="s.expiryEstimated" class="muted" style="font-size:.7rem"> est</span></td>
          <td style="text-align:right;white-space:nowrap">
            <button class="secondary sm" @click="openConsume(s)">Use</button>
            <button class="ghost sm" @click="del(s)">✕</button></td>
        </tr>
      </tbody>
    </table>
  </div>
  <div v-else class="empty"><div class="ico">🥫</div><p>No stock here yet.</p>
    <button style="margin-top:10px" @click="showAdd = true">Add your first item</button></div>

  <!-- Add stock -->
  <div v-if="showAdd" class="modal-backdrop" @click.self="showAdd = false">
    <div class="card modal">
      <h2>Add stock</h2>
      <div class="row">
        <label class="field" style="flex:1"><span>Barcode (optional)</span>
          <input v-model="form.barcode" placeholder="scan or type" @keyup.enter="lookupBarcode('form')" @blur="lookupBarcode('form')" /></label>
        <button type="button" class="secondary" style="align-self:flex-end;height:38px" @click="startScan">📷 Scan</button>
      </div>
      <div v-if="scanning" class="scanbox">
        <video ref="scanVideo" muted playsinline></video>
        <button type="button" class="ghost sm" @click="stopScan">Stop</button>
      </div>
      <label class="field"><span>What is it?</span><input v-model="form.productName" placeholder="e.g. Whole milk" autofocus /></label>
      <div class="row">
        <label class="field" style="flex:1"><span>Category</span>
          <select v-model="form.category"><option v-for="c in meta.categories" :key="c" :value="c">{{ c }}</option></select></label>
        <label class="field" style="flex:1"><span>Storage</span>
          <select v-model="form.storageMethod"><option v-for="s in meta.storageMethods" :key="s" :value="s">{{ s.replace('_',' ') }}</option></select></label>
      </div>
      <div class="row">
        <label class="field" style="width:110px"><span>Quantity</span><input type="number" min="0" v-model.number="form.quantity" /></label>
        <label class="field" style="width:110px"><span>Unit</span>
          <select v-model="form.unit"><option v-for="u in meta.units" :key="u" :value="u">{{ u }}</option></select></label>
        <label class="field" style="flex:1"><span>Ripeness (optional)</span>
          <select v-model="form.state"><option value="">—</option>
            <option v-for="st in meta.lifecycleStates" :key="st" :value="st">{{ st }}</option></select></label>
      </div>
      <div class="row">
        <label class="field" style="flex:1"><span>Location</span>
          <select v-model="form.locationId"><option value="">Unassigned</option>
            <option v-for="l in locations" :key="l.id" :value="l.id">{{ l.name }}</option></select></label>
        <label class="field" style="flex:1"><span>Expiry (blank = estimate)</span><input type="date" v-model="form.expiryDate" /></label>
      </div>
      <p class="muted" style="font-size:.8rem;margin-top:-4px">Leave expiry blank — Edibl estimates it from the category + storage, and learns from your own history.</p>
      <div class="row" style="justify-content:flex-end;margin-top:6px">
        <button class="secondary" @click="showAdd=false; stopScan()">Cancel</button>
        <button :disabled="!form.productName.trim()" @click="add">Add</button></div>
    </div>
  </div>

  <!-- Bulk add (generic) -->
  <div v-if="showBulk" class="modal-backdrop" @click.self="showBulk = false">
    <div class="card modal">
      <h2>⧉ Bulk add</h2>
      <p class="muted" style="margin-top:0">Add many items at once — a grocery haul, a farm box, or a butchered animal into the freezer. Shared settings apply to every row; override per row as needed.</p>
      <div class="row">
        <label class="field" style="flex:1"><span>Default storage</span>
          <select v-model="bulk.shared.storageMethod"><option v-for="s in meta.storageMethods" :key="s" :value="s">{{ s.replace('_',' ') }}</option></select></label>
        <label class="field" style="flex:1"><span>Default category</span>
          <select v-model="bulk.shared.category"><option v-for="c in meta.categories" :key="c" :value="c">{{ c }}</option></select></label>
      </div>
      <div class="row">
        <label class="field" style="flex:1"><span>Location</span>
          <select v-model="bulk.shared.locationId"><option value="">Unassigned</option>
            <option v-for="l in locations" :key="l.id" :value="l.id">{{ l.name }}</option></select></label>
        <label class="field" style="flex:1"><span>Source (optional)</span><input v-model="bulk.shared.source" placeholder="e.g. Costco, farm share, half a cow" /></label>
      </div>
      <div class="divider"></div>
      <div v-for="(r,i) in bulk.rows" :key="i" class="row" style="margin-bottom:8px">
        <input v-model="r.name" placeholder="Item name" style="flex:2" />
        <input type="number" v-model.number="r.quantity" placeholder="qty" style="width:80px" />
        <select v-model="r.unit" style="width:90px"><option v-for="u in meta.units" :key="u" :value="u">{{ u }}</option></select>
        <select v-model="r.storageMethod" style="width:130px" title="override storage">
          <option value="">(default)</option>
          <option v-for="s in meta.storageMethods" :key="s" :value="s">{{ s.replace('_',' ') }}</option></select>
      </div>
      <div class="row" style="justify-content:space-between">
        <button class="secondary sm" @click="addBulkRow">＋ Another row</button>
        <label class="muted sm" style="cursor:pointer">📋 Paste list
          <input type="file" hidden @change="(e)=>{const f=e.target.files[0]; if(f){f.text().then(pasteBulk)}}" accept=".txt,.csv" /></label>
      </div>
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
      <div class="row" style="align-items:center">
        <span class="muted sm">Update ripeness:</span>
        <button v-for="st in meta.lifecycleStates" :key="st" class="ghost sm"
          @click="setState(consumeFor, st)">{{ st }}</button>
      </div>
      <div class="row" style="justify-content:flex-end;margin-top:10px">
        <button class="ghost" @click="consumeFor = null">Close</button></div>
    </div>
  </div>
</template>

<style scoped>
.toast { background: rgba(47,158,87,.14); border: 1px solid var(--primary, #2f9e57);
  color: var(--text, #eee); padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; }
.chip { margin-left: 6px; }
.scanbox { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; margin-bottom: 10px; }
.scanbox video { width: 100%; max-height: 200px; border-radius: 8px; background: #000; }
.outcome-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
.outcome-grid button { width: 100%; }
.sm { font-size: .8rem; }
</style>
