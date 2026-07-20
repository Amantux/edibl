<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const locs = ref([])
const meta = ref({ locationKinds: [] })
const show = ref(false)
const form = ref({ name: '', kind: 'fridge', parentId: '' })
const icons = { site: '🏠', room: '🚪', fridge: '🧊', freezer: '❄️', pantry: '🥫', wine_cellar: '🍷', cupboard: '🗄️', other: '📍' }

async function load() { locs.value = await api.get('/locations') }
onMounted(async () => { meta.value = await api.get('/meta'); await load() })
async function create() {
  if (!form.value.name.trim()) return
  const body = { ...form.value }; if (!body.parentId) delete body.parentId
  await api.post('/locations', body); show.value = false
  form.value = { name: '', kind: 'fridge', parentId: '' }; await load()
}
async function del(l) { if (confirm(`Delete ${l.name} and its contents?`)) { await api.del('/locations/'+l.id); await load() } }
</script>

<template>
  <div class="page-head"><h1>📍 Locations</h1><span class="badge">{{ locs.length }}</span>
    <div class="grow"></div><button @click="show = true">＋ New location</button></div>
  <p class="muted" style="margin-top:-8px">Sites, rooms, fridges, freezers, wine cellars — nest them however your homes are laid out.</p>

  <div v-if="locs.length" class="card-grid">
    <div v-for="l in locs" :key="l.id" class="card" style="margin:0">
      <div class="row"><div style="font-size:1.6rem">{{ icons[l.kind] || '📍' }}</div>
        <div style="flex:1"><div style="font-weight:650">{{ l.name }}</div>
          <div class="muted" style="font-size:.8rem">{{ l.kind.replace('_',' ') }}<span v-if="l.parent"> · in {{ l.parent.name }}</span></div></div>
        <button class="ghost sm" @click="del(l)">✕</button></div>
      <div class="row" style="margin-top:10px;gap:6px">
        <span class="badge">{{ l.stockCount }} items</span>
        <span v-if="l.childCount" class="badge">{{ l.childCount }} sub</span>
        <span v-if="l.tempC != null" class="badge">{{ l.tempC }}°C</span></div>
    </div>
  </div>
  <div v-else class="empty"><div class="ico">📍</div><p>No locations yet — add a fridge, freezer, or pantry to start.</p></div>

  <div v-if="show" class="modal-backdrop" @click.self="show = false">
    <div class="card modal">
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
