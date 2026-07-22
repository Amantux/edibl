<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { ui } from '../ui'
const router = useRouter()
const locs = ref([])
const loading = ref(true)
const meta = ref({ locationKinds: [] })
const show = ref(false)
const form = ref({ name: '', kind: 'fridge', parentId: '' })
const icons = { site: '🏠', room: '🚪', fridge: '🧊', freezer: '❄️', pantry: '🥫', wine_cellar: '🍷', cupboard: '🗄️', other: '📍' }

// Drill-in: click a location to see what's actually in it.
const drillLoc = ref(null)
const drillItems = ref([])
const drillBusy = ref(false)
async function openLoc(l) {
  drillLoc.value = l; drillItems.value = []; drillBusy.value = true
  try { drillItems.value = (await api.get(`/stock?locationId=${l.id}`)).items || [] } finally { drillBusy.value = false }
}
function expLabel(s) {
  if (s.daysToExpiry == null) return '—'
  return s.daysToExpiry < 0 ? 'expired' : s.daysToExpiry + 'd'
}
function reconcileHere(l) { router.push(`/stock?reconcile=${l.id}`) }
function addHere() { router.push('/stock?add=1') }

async function load() {
  loading.value = true
  try { locs.value = await api.get('/locations') }
  catch (e) { ui.error(e.message || 'Could not load locations.') }
  finally { loading.value = false }
}
onMounted(async () => {
  try { const [m] = await Promise.all([api.get('/meta'), load()]); meta.value = m }
  catch (e) { /* load() surfaces its own error */ }
})
async function create() {
  if (!form.value.name.trim()) return
  const body = { ...form.value }; if (!body.parentId) delete body.parentId
  try {
    await api.post('/locations', body); show.value = false
    form.value = { name: '', kind: 'fridge', parentId: '' }
    ui.success('Location added.'); await load()
  } catch (e) { ui.error(e.message || 'Could not add the location.') }
}
async function del(l) {
  if (!confirm(`Delete ${l.name} and its contents?`)) return
  try { await api.del('/locations/' + l.id); ui.info(`Deleted ${l.name}.`); await load() }
  catch (e) { ui.error(e.message || 'Could not delete.') }
}
</script>

<template>
  <div class="page-head"><h1>📍 Locations</h1><span class="badge">{{ locs.length }}</span>
    <div class="grow"></div><button @click="show = true">＋ New location</button></div>
  <p class="muted" style="margin-top:-8px">Sites, rooms, fridges, freezers, wine cellars — nest them however your homes are laid out.</p>

  <div v-if="loading" class="muted" style="padding:12px">Loading locations…</div>
  <div v-else-if="locs.length" class="card-grid">
    <div v-for="l in locs" :key="l.id" class="card loc-card" style="margin:0"
      role="button" tabindex="0" @click="openLoc(l)" @keydown.enter="openLoc(l)">
      <div class="row"><div style="font-size:1.6rem">{{ icons[l.kind] || '📍' }}</div>
        <div style="flex:1"><div style="font-weight:650">{{ l.name }}</div>
          <div class="muted" style="font-size:.8rem">{{ l.kind.replace('_',' ') }}<span v-if="l.parent"> · in {{ l.parent.name }}</span></div></div>
        <button class="ghost sm" @click.stop="del(l)">✕</button></div>
      <div class="row" style="margin-top:10px;gap:6px">
        <span class="badge">{{ l.stockCount }} items</span>
        <span v-if="l.childCount" class="badge">{{ l.childCount }} sub</span>
        <span v-if="l.tempC != null" class="badge">{{ l.tempC }}°C</span>
        <span class="grow"></span><span class="muted" style="font-size:.72rem">view →</span></div>
    </div>
  </div>
  <div v-else class="empty"><div class="ico">📍</div><p>No locations yet — add a fridge, freezer, or pantry to start.</p>
    <button style="margin-top:10px" @click="show = true">Add your first location</button></div>

  <!-- Drill-in drawer: what's actually in this place -->
  <div v-if="drillLoc" class="drawer-backdrop" @click.self="drillLoc=null">
    <div class="drawer" v-trap="() => drillLoc=null" :aria-label="`Contents of ${drillLoc.name}`">
      <div class="page-head"><h1 style="font-size:1.2rem">{{ icons[drillLoc.kind] || '📍' }} {{ drillLoc.name }}</h1>
        <div class="grow"></div><button class="ghost sm" @click="drillLoc=null">✕</button></div>
      <div class="row wrap" style="gap:8px;margin-bottom:14px">
        <button class="secondary sm" @click="addHere">＋ Add here</button>
        <button class="secondary sm" @click="reconcileHere(drillLoc)">📋 Reconcile this place</button>
      </div>
      <div v-if="drillBusy" class="muted">Loading…</div>
      <table v-else-if="drillItems.length">
        <thead><tr><th>Item</th><th>Amount</th><th>Expiry</th></tr></thead>
        <tbody>
          <tr v-for="s in drillItems" :key="s.id">
            <td>{{ s.product?.name }}<span v-if="s.packageState==='opened'" class="chip">open</span></td>
            <td>{{ s.quantityKind==='exact' ? (s.quantity+' '+s.unit) : s.quantityText }}</td>
            <td><span class="badge" :class="s.expiryStatus">{{ expLabel(s) }}</span></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty"><div class="ico">📦</div><p>Nothing here yet.</p>
        <button style="margin-top:8px" @click="addHere">Add something</button></div>
    </div>
  </div>

  <div v-if="show" class="modal-backdrop" @click.self="show = false">
    <div class="card modal" v-trap="() => show = false" aria-label="New location">
      <h2>New location</h2>
      <label class="field"><span>Name</span><input v-model="form.name" placeholder="e.g. Kitchen Fridge" autofocus @keyup.enter="create" /></label>
      <label class="field"><span>Kind</span><select v-model="form.kind">
        <option v-for="k in meta.locationKinds" :key="k" :value="k">{{ k.replace('_',' ') }}</option></select></label>
      <label class="field"><span>Inside (optional)</span><select v-model="form.parentId">
        <option value="">Top-level site</option><option v-for="l in locs" :key="l.id" :value="l.id">{{ l.name }}</option></select></label>
      <div class="row" style="justify-content:flex-end"><button class="secondary" @click="show=false">Cancel</button>
        <button :disabled="!form.name.trim()" @click="create">Create</button></div>
    </div>
  </div>
</template>

<style scoped>
.loc-card { cursor: pointer; transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease; }
.loc-card:hover { transform: translateY(-2px); box-shadow: var(--shadow); border-color: var(--accent); }
</style>
