<script setup>
import { ref, onMounted } from 'vue'
import { api, apiUrl, getToken } from '../api'

// ── Database backend + migrate SQLite → an empty PostgreSQL ───────────────────
const dbBackend = ref('sqlite')
const pgUrl = ref('')
const pgBusy = ref(false)
const pgResult = ref(null)   // { total, tables, next }
const pgMsg = ref('')
async function loadDbBackend() {
  try { dbBackend.value = (await api.get('/meta')).dbBackend || 'sqlite' } catch (e) { /* leave default */ }
}
async function migratePg() {
  if (!pgUrl.value.trim()) return
  if (!confirm('Copy all data into this PostgreSQL database? It must be empty. Your current SQLite data is left untouched.')) return
  pgBusy.value = true; pgMsg.value = ''; pgResult.value = null
  try {
    pgResult.value = await api.post('/migrate/postgres', { targetUrl: pgUrl.value.trim() })
    pgMsg.value = ''
  } catch (e) { pgMsg.value = '⚠️ ' + (e.message || 'Migration failed.') } finally { pgBusy.value = false }
}

// ── Export / import a portable snapshot ──────────────────────────────────────
const importing = ref(false)
const result = ref('')
function triggerBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}
async function exportJson() {
  triggerBlob(new Blob([JSON.stringify(await api.get('/export'), null, 2)], { type: 'application/json' }), 'edibl-export.json')
}
async function exportCsv() {
  const res = await fetch(apiUrl('/export/stock.csv'), { headers: getToken() ? { Authorization: getToken() } : {} })
  triggerBlob(await res.blob(), 'edibl-stock.csv')
}
async function backupDb() {
  const res = await fetch(apiUrl('/export/backup.db'), { headers: getToken() ? { Authorization: getToken() } : {} })
  if (!res.ok) return
  const stamp = new Date().toISOString().slice(0, 10)
  triggerBlob(await res.blob(), `edibl-backup-${stamp}.db`)
}
async function importFile(e) {
  const file = e.target.files?.[0]; e.target.value = ''
  if (!file) return
  importing.value = true; result.value = ''
  try {
    const c = (await api.post('/import', JSON.parse(await file.text()))).imported
    result.value = `✓ Imported ${c.products} products, ${c.locations} locations, ${c.stock} stock lots, ${c.shopping} shopping items.`
  } catch (err) { result.value = '⚠️ Import failed: ' + (err.message || 'invalid file') } finally { importing.value = false }
}

onMounted(loadDbBackend)
</script>

<template>
  <div class="page-head"><h1>🗄️ Database</h1></div>

  <div class="card" v-if="dbBackend === 'sqlite'">
    <h2>🐘 Migrate to PostgreSQL</h2>
    <p class="muted" style="margin-top:0">Edibl runs on its built-in <strong>SQLite</strong> database (recommended for most).
      To move to an external <strong>PostgreSQL</strong>, enter an <strong>empty</strong> Postgres database below and click Migrate —
      Edibl copies everything across, leaving your SQLite data untouched. Then set the add-on's
      <code>database_url</code> (or <code>EDIBL_DATABASE_URL</code>) to the same URL and restart to run on Postgres.</p>
    <label class="field"><span>Target PostgreSQL URL</span>
      <input v-model="pgUrl" placeholder="postgresql+psycopg://user:pass@host:5432/dbname" /></label>
    <div class="row" style="justify-content:flex-end">
      <button :disabled="pgBusy || !pgUrl.trim()" @click="migratePg">{{ pgBusy ? 'Migrating…' : 'Migrate data' }}</button></div>
    <div v-if="pgResult" style="border:1px solid var(--primary,#2f9e57);border-radius:8px;padding:10px 12px;margin-top:10px;background:rgba(47,158,87,.10)">
      <p style="margin:0 0 4px"><strong>✓ Copied {{ pgResult.total }} rows.</strong></p>
      <p class="muted" style="margin:0;font-size:.85rem">{{ pgResult.next }}</p>
    </div>
    <p v-if="pgMsg" class="muted" style="font-size:.85rem;margin-top:8px">{{ pgMsg }}</p>
  </div>
  <div class="card" v-else>
    <h2>🐘 PostgreSQL</h2>
    <p class="muted" style="margin:0">Edibl is running on an external PostgreSQL database.</p>
  </div>

  <div class="card">
    <h2>Export</h2>
    <p class="muted" style="margin-top:0">Download a portable snapshot of your inventory — to keep, or to move to another Edibl instance. (Home Assistant already backs up the add-on's storage automatically.)</p>
    <div class="row wrap">
      <button @click="exportJson">⬇️ Export JSON (full)</button>
      <button class="secondary" @click="exportCsv">⬇️ Stock as CSV</button>
      <button class="secondary" @click="backupDb" title="A consistent copy of the whole database file">💾 Backup database</button>
    </div>
  </div>

  <div class="card">
    <h2>Import</h2>
    <p class="muted" style="margin-top:0">Restore from an exported JSON file. Import is <strong>additive</strong> — it creates products, locations, stock, and shopping items that don't already exist, and never deletes anything.</p>
    <label class="secondary" style="cursor:pointer;display:inline-block;padding:9px 15px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--surface);font-weight:600;font-size:.9rem">
      {{ importing ? 'Importing…' : '⬆️ Choose export file' }}
      <input type="file" hidden accept=".json,application/json" :disabled="importing" @change="importFile" />
    </label>
    <p v-if="result" style="margin-top:10px">{{ result }}</p>
  </div>
</template>

<style scoped>
.card .field input:not([type="checkbox"]):not([type="file"]),
.card .field select { max-width: 520px; }
.card label.field > span { color: var(--text); font-size: .84rem; }
</style>
