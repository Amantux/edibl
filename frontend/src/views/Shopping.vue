<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { ui } from '../ui'
import { useLiveRefresh } from '../live'
const items = ref([])
const newItem = ref({ name: '', quantity: 1, unit: 'count' })
const copied = ref(false)
const exportText = ref('')
const loading = ref(true)

async function load(silent = false) {
  if (!silent) loading.value = true
  try { items.value = await api.get('/shopping') }
  catch (e) { if (!silent) ui.error(e.message || 'Could not load the shopping list.') }
  finally { if (!silent) loading.value = false }
}
onMounted(() => load())
useLiveRefresh(() => load(true))   // live sync from chat / other devices

// Friendly label for the source "tag" on a list item.
function tagLabel(src) { return src === 'low_stock' ? 'low' : src.replace('_', ' ') }

async function add() {
  if (!newItem.value.name.trim()) return
  try {
    await api.post('/shopping', { ...newItem.value })
    newItem.value = { name: '', quantity: 1, unit: 'count' }; await load()
  } catch (e) { ui.error(e.message || 'Could not add the item.') }
}
async function remove(i) {
  try { await api.del('/shopping/' + i.id); await load() } catch (e) { ui.error(e.message || 'Could not remove.') }
}
async function purchased(i) {
  try { await api.put('/shopping/' + i.id, { status: 'purchased' }); await load() }
  catch (e) { ui.error(e.message || 'Could not update.') }
}
async function suggest() {
  try {
    const r = await api.post('/shopping/suggest'); await load()
    ui.success(r.added ? `Added ${r.added} low-stock item(s).` : 'Nothing new to suggest.')
  } catch (e) { ui.error(e.message || 'Could not suggest items.') }
}

async function copyForDelivery() {
  const res = await api.get('/shopping/export?format=json')
  exportText.value = res.text
  try {
    await navigator.clipboard.writeText(res.text)
    copied.value = true; setTimeout(() => (copied.value = false), 2500)
  } catch (e) { /* fall back to showing the block */ }
}
</script>

<template>
  <div class="page-head"><h1>🛒 Shopping list</h1><span class="badge">{{ items.length }}</span>
    <div class="grow"></div>
    <button class="secondary" @click="suggest">✨ Suggest from low stock</button>
    <button @click="copyForDelivery">{{ copied ? '✓ Copied!' : '📋 Copy for delivery' }}</button></div>

  <div class="card">
    <form class="row" style="margin-bottom:14px" @submit.prevent="add">
      <label class="sr-only" for="sl-add">Add an item</label>
      <input id="sl-add" v-model="newItem.name" placeholder="Add an item…" style="flex:2" />
      <label class="sr-only" for="sl-qty">Quantity</label>
      <input id="sl-qty" type="number" min="0" v-model.number="newItem.quantity" style="width:90px" />
      <button type="submit">Add</button>
    </form>
    <div v-if="loading" class="muted">Loading…</div>
    <table v-else-if="items.length">
      <tbody>
        <tr v-for="i in items" :key="i.id">
          <td><strong>{{ i.name }}</strong> <span class="muted">{{ i.quantity }} {{ i.unit }}</span>
            <span v-if="i.source!=='manual'" class="chip" :class="{low: i.source==='low_stock'}" style="margin-left:6px">{{ tagLabel(i.source) }}</span>
            <span v-if="i.note" class="muted" style="font-size:.78rem"> · {{ i.note }}</span></td>
          <td style="text-align:right;white-space:nowrap">
            <button class="secondary sm" @click="purchased(i)">Got it</button>
            <button class="ghost sm" :aria-label="`Remove ${i.name} from list`" @click="remove(i)">✕</button></td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty"><div class="ico">🛒</div><p>List is empty. Add items, or let Edibl suggest what you're low on.</p></div>
  </div>

  <div v-if="exportText" class="card">
    <h2>Paste into Uber Eats / Instacart</h2>
    <p class="muted" style="margin-top:0">Copied to your clipboard — one item per line, ready to paste into a grocery app's search.</p>
    <pre class="export">{{ exportText }}</pre>
  </div>
</template>

<style scoped>
.chip.low { background: rgba(217,119,6,.14); color: var(--warning); }
</style>
