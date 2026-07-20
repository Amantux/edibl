<script setup>
import { ref, onMounted } from 'vue'
import { api, apiUrl, getToken } from '../api'

const importing = ref(false)
const result = ref('')
const cfg = ref(null)

onMounted(async () => {
  try { cfg.value = await api.get('/assistant/config') } catch (e) { cfg.value = { enabled: false } }
})

function triggerBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

async function exportJson() {
  const data = await api.get('/export')
  triggerBlob(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    'edibl-export.json')
}
async function exportCsv() {
  const res = await fetch(apiUrl('/export/stock.csv'),
    { headers: getToken() ? { Authorization: getToken() } : {} })
  triggerBlob(await res.blob(), 'edibl-stock.csv')
}
async function importFile(e) {
  const file = e.target.files?.[0]; e.target.value = ''
  if (!file) return
  importing.value = true; result.value = ''
  try {
    const data = JSON.parse(await file.text())
    const c = (await api.post('/import', data)).imported
    result.value = `✓ Imported ${c.products} products, ${c.locations} locations, ${c.stock} stock lots, ${c.shopping} shopping items.`
  } catch (err) {
    result.value = '⚠️ Import failed: ' + (err.message || 'invalid file')
  } finally { importing.value = false }
}
</script>

<template>
  <div class="page-head"><h1>⚙️ Settings</h1></div>

  <div class="card">
    <h2>Chat assistant</h2>
    <div v-if="cfg">
      <div class="row wrap" style="gap:8px;margin-bottom:8px">
        <span class="badge" :class="cfg.enabled ? 'fresh' : 'expired'">
          {{ cfg.enabled ? 'connected' : 'not configured' }}</span>
        <span class="chip">provider: {{ cfg.provider }}</span>
        <span v-if="cfg.model" class="chip">model: {{ cfg.model }}</span>
        <span class="chip">{{ cfg.tools ? 'full CRUD' : 'completion-only' }}</span>
      </div>
      <p class="muted" style="margin:0;font-size:.85rem">
        Configure the provider in Home Assistant → <strong>Settings → Add-ons → Edibl →
        Configuration</strong> (or the <code>EDIBL_LLM_*</code> env vars if standalone).
        Options: <code>ollama</code>, <code>openai</code>, <code>anthropic</code>, or
        <code>homeassistant</code> (reuse HA's own conversation agent).
      </p>
    </div>
    <div v-else class="muted">Loading…</div>
  </div>

  <div class="card">
    <h2>Export</h2>
    <p class="muted" style="margin-top:0">Download a portable snapshot of your inventory — to keep, or to move to another Edibl instance. (Home Assistant already backs up the add-on's storage automatically.)</p>
    <div class="row wrap">
      <button @click="exportJson">⬇️ Export JSON (full)</button>
      <button class="secondary" @click="exportCsv">⬇️ Stock as CSV</button>
    </div>
  </div>

  <div class="card">
    <h2>Import</h2>
    <p class="muted" style="margin-top:0">Restore from an exported JSON file. Import is <strong>additive</strong> — it creates products, locations, stock, and shopping items that don't already exist, and never deletes anything.</p>
    <label class="secondary" style="cursor:pointer;display:inline-block;padding:9px 15px;border-radius:9px">
      {{ importing ? 'Importing…' : '⬆️ Choose export file' }}
      <input type="file" hidden accept=".json,application/json" :disabled="importing" @change="importFile" />
    </label>
    <p v-if="result" style="margin-top:10px">{{ result }}</p>
  </div>
</template>
