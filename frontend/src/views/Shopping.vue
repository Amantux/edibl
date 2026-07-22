<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const items = ref([])
const newItem = ref({ name: '', quantity: 1, unit: 'count' })
const copied = ref(false)
const exportText = ref('')

async function load() { items.value = await api.get('/shopping') }
onMounted(load)

// Friendly label for the source "tag" on a list item.
function tagLabel(src) { return src === 'low_stock' ? 'low' : src.replace('_', ' ') }

async function add() {
  if (!newItem.value.name.trim()) return
  await api.post('/shopping', { ...newItem.value })
  newItem.value = { name: '', quantity: 1, unit: 'count' }; await load()
}
async function remove(i) { await api.del('/shopping/' + i.id); await load() }
async function purchased(i) { await api.put('/shopping/' + i.id, { status: 'purchased' }); await load() }
async function suggest() { await api.post('/shopping/suggest'); await load() }

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
    <div class="row" style="margin-bottom:14px">
      <input v-model="newItem.name" placeholder="Add an item…" style="flex:2" @keyup.enter="add" />
      <input type="number" v-model.number="newItem.quantity" style="width:90px" />
      <button @click="add">Add</button>
    </div>
    <table v-if="items.length">
      <tbody>
        <tr v-for="i in items" :key="i.id">
          <td><strong>{{ i.name }}</strong> <span class="muted">{{ i.quantity }} {{ i.unit }}</span>
            <span v-if="i.source!=='manual'" class="chip" :class="{low: i.source==='low_stock'}" style="margin-left:6px">{{ tagLabel(i.source) }}</span>
            <span v-if="i.note" class="muted" style="font-size:.78rem"> · {{ i.note }}</span></td>
          <td style="text-align:right;white-space:nowrap">
            <button class="secondary sm" @click="purchased(i)">Got it</button>
            <button class="ghost sm" @click="remove(i)">✕</button></td>
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
