<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'

const items = ref([])
const meta = ref({ categories: [], units: [], storageMethods: [], locationKinds: [] })
const locations = ref([])
const loading = ref(true)
const filter = ref({ category: '', storageMethod: '', status: '', view: 'all' })
const showAdd = ref(false)
const showButcher = ref(false)
const form = ref(blankForm())
const butcher = ref({ source: '', animal: '', locationId: '', cuts: [{ cut: '', weightG: null, quantity: 1 }] })

function blankForm() {
  return { productName: '', category: 'other', quantity: 1, unit: 'count',
    storageMethod: 'refrigerated', locationId: '', purchaseDate: '', expiryDate: '', cost: null }
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

async function add() {
  if (!form.value.productName.trim()) return
  const body = { ...form.value }
  if (!body.purchaseDate) delete body.purchaseDate
  if (!body.expiryDate) delete body.expiryDate
  if (!body.locationId) delete body.locationId
  await api.post('/stock', body)
  showAdd.value = false; form.value = blankForm(); await load()
}
async function consume(s) {
  await api.post(`/stock/${s.id}/consume`, {})
  await load()
}
async function del(s) {
  if (!confirm(`Remove ${s.product?.name}?`)) return
  await api.del('/stock/' + s.id); await load()
}
function addCut() { butcher.value.cuts.push({ cut: '', weightG: null, quantity: 1 }) }
async function submitButcher() {
  const cuts = butcher.value.cuts.filter((c) => c.cut.trim())
  if (!cuts.length) return
  await api.post('/stock/butcher', { ...butcher.value, cuts })
  showButcher.value = false
  butcher.value = { source: '', animal: '', locationId: '', cuts: [{ cut: '', weightG: null, quantity: 1 }] }
  filter.value.view = 'freezer'; await load()
}
const title = computed(() => filter.value.view === 'freezer' ? '❄️ Freezer & long-term' :
  filter.value.view === 'wine' ? '🍷 Wine & cellar' : 'Stock')
</script>

<template>
  <div class="page-head"><h1>{{ title }}</h1><span class="badge">{{ items.length }}</span>
    <div class="grow"></div>
    <button class="secondary" @click="showButcher = true">🔪 Butcher session</button>
    <button @click="showAdd = true">＋ Add stock</button></div>

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
            <span v-if="s.attrs?.cut" class="muted"> · {{ s.attrs.animal }} {{ s.attrs.cut }}</span></td>
          <td class="muted">{{ s.location?.name || '—' }}</td>
          <td>{{ s.quantity }} {{ s.unit }}</td>
          <td><span class="chip">{{ s.storageMethod.replace('_',' ') }}</span></td>
          <td><span class="badge" :class="s.expiryStatus">
            {{ s.daysToExpiry == null ? '—' : (s.daysToExpiry < 0 ? 'expired' : s.daysToExpiry+'d') }}</span>
            <span v-if="s.expiryEstimated" class="muted" style="font-size:.7rem"> est</span></td>
          <td style="text-align:right;white-space:nowrap">
            <button class="secondary sm" @click="consume(s)">Use</button>
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
        <label class="field" style="flex:1"><span>Location</span>
          <select v-model="form.locationId"><option value="">Unassigned</option>
            <option v-for="l in locations" :key="l.id" :value="l.id">{{ l.name }}</option></select></label>
      </div>
      <div class="row">
        <label class="field" style="flex:1"><span>Purchased</span><input type="date" v-model="form.purchaseDate" /></label>
        <label class="field" style="flex:1"><span>Expiry (blank = estimate)</span><input type="date" v-model="form.expiryDate" /></label>
      </div>
      <p class="muted" style="font-size:.8rem;margin-top:-4px">Leave expiry blank and we'll estimate it from the category + storage method.</p>
      <div class="row" style="justify-content:flex-end;margin-top:6px">
        <button class="secondary" @click="showAdd=false">Cancel</button>
        <button :disabled="!form.productName.trim()" @click="add">Add</button></div>
    </div>
  </div>

  <!-- Butcher session -->
  <div v-if="showButcher" class="modal-backdrop" @click.self="showButcher = false">
    <div class="card modal">
      <h2>🔪 Butchering session</h2>
      <p class="muted" style="margin-top:0">One animal → many vacuum-sealed frozen cuts, each with a long estimated expiry.</p>
      <div class="row">
        <label class="field" style="flex:1"><span>Source</span><input v-model="butcher.source" placeholder="e.g. half a cow" /></label>
        <label class="field" style="flex:1"><span>Animal</span><input v-model="butcher.animal" placeholder="beef" /></label>
      </div>
      <label class="field"><span>Freezer location</span>
        <select v-model="butcher.locationId"><option value="">Unassigned</option>
          <option v-for="l in locations" :key="l.id" :value="l.id">{{ l.name }}</option></select></label>
      <div class="divider"></div>
      <div v-for="(c,i) in butcher.cuts" :key="i" class="row" style="margin-bottom:8px">
        <input v-model="c.cut" placeholder="Cut (e.g. Ribeye)" style="flex:2" />
        <input type="number" v-model.number="c.weightG" placeholder="grams" style="width:100px" />
        <input type="number" v-model.number="c.quantity" placeholder="packs" style="width:90px" />
      </div>
      <button class="secondary sm" @click="addCut">＋ Another cut</button>
      <div class="row" style="justify-content:flex-end;margin-top:14px">
        <button class="secondary" @click="showButcher=false">Cancel</button>
        <button @click="submitButcher">Freeze it all</button></div>
    </div>
  </div>
</template>
