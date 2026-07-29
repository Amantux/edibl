<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { ui } from '../ui'
import { useLiveRefresh } from '../live'

const data = ref(null)
const loading = ref(true)
const err = ref('')
const months = ref(12)

async function load(silent = false) {
  if (!silent) { loading.value = true; err.value = '' }
  try {
    data.value = await api.get(`/stock/insights?months=${months.value}`)
  } catch (e) {
    err.value = e.message || 'Could not load spend insights.'
    if (!silent) ui.error(err.value)
  } finally { loading.value = false }
}
onMounted(() => load())
useLiveRefresh(() => load(true))

const currency = computed(() => data.value?.currency || 'USD')
const fmt = (n) => {
  if (n == null) return '—'
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency.value,
      maximumFractionDigits: 2 }).format(n)
  } catch { return `${currency.value} ${Number(n).toFixed(2)}` }
}
// Compact per-unit price (4dp source, show up to 2dp).
const fmtUnit = (n) => (n == null ? '—' : fmt(Number(n)))

const monthLabel = (mk) => {
  if (!mk) return ''
  const [y, m] = mk.split('-')
  return new Date(Number(y), Number(m) - 1, 1)
    .toLocaleDateString(undefined, { month: 'short' }) + (m === '01' ? ` '${y.slice(2)}` : '')
}

const spendSeries = computed(() => data.value?.spendByMonth || [])
const maxSpend = computed(() => Math.max(1, ...spendSeries.value.map((p) => p.spend || 0)))
const byCategory = computed(() => data.value?.spendByCategory || [])
const maxCat = computed(() => Math.max(1, ...byCategory.value.map((c) => c.spend || 0)))
const valueByCat = computed(() => data.value?.valueOnHand?.byCategory || [])
const history = computed(() => data.value?.priceHistory || [])

// A product's price points → an SVG polyline (normalized), for a mini trend.
function spark(points) {
  const vals = points.map((p) => p.price).filter((v) => v != null)
  if (vals.length < 2) return ''
  const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1
  const w = 120, h = 26
  return vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w
    const y = h - ((v - min) / span) * (h - 4) - 2
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}
</script>

<template>
  <div class="page-head"><h1>Spend &amp; value</h1>
    <span v-if="data" class="muted" style="font-size:.9rem">· priced in {{ currency }}</span>
    <div class="grow"></div>
    <label class="field" style="width:150px"><span class="sr-only">Window</span>
      <select v-model.number="months" @change="load()">
        <option :value="6">Last 6 months</option>
        <option :value="12">Last 12 months</option>
        <option :value="24">Last 24 months</option>
      </select></label>
  </div>

  <template v-if="data">
    <div class="stat-grid">
      <div class="stat"><div class="value">{{ fmt(data.valueOnHand.total) }}</div>
        <div class="label">Value on hand</div></div>
      <div class="stat"><div class="value">{{ fmt(data.spendThisMonth) }}</div>
        <div class="label">Spent this month</div></div>
      <div class="stat"><div class="value" :style="data.wasteCost ? 'color:var(--danger)' : ''">
          {{ fmt(data.wasteCost) }}</div>
        <div class="label">Waste cost (lost)</div></div>
      <div class="stat"><div class="value" :style="data.valueOnHand.expiredUnused ? 'color:var(--warning)' : ''">
          {{ fmt(data.valueOnHand.expiredUnused) }}</div>
        <div class="label">Expired, unused</div></div>
    </div>

    <div class="card">
      <h3>Spend over time</h3>
      <p class="muted" style="font-size:.85rem;margin-top:-4px">What you bought each month, by purchase date.</p>
      <div class="spendbars">
        <div v-for="p in spendSeries" :key="p.month" class="spendbar"
             :title="`${monthLabel(p.month)}: ${fmt(p.spend)}`">
          <div class="spendbar-fill" :style="{ height: (6 + (p.spend / maxSpend) * 120) + 'px' }"></div>
          <div class="spendbar-x">{{ monthLabel(p.month) }}</div>
        </div>
      </div>
    </div>

    <div class="card-grid" style="margin-top:16px">
      <div class="card">
        <h3>Spend by category</h3>
        <p class="muted" style="font-size:.85rem;margin-top:-4px">Over the last {{ data.windowMonths }} months.</p>
        <div v-if="!byCategory.length" class="muted">No priced purchases yet.</div>
        <div v-for="c in byCategory" :key="c.category" class="hbar-row">
          <div class="hbar-label">{{ c.category }}</div>
          <div class="hbar-track"><div class="hbar-fill" :style="{ width: (c.spend / maxCat * 100) + '%' }"></div></div>
          <div class="hbar-val">{{ fmt(c.spend) }}</div>
        </div>
      </div>

      <div class="card">
        <h3>Value on hand by category</h3>
        <p class="muted" style="font-size:.85rem;margin-top:-4px">Current stock, valued at purchase price.</p>
        <div v-if="!valueByCat.length" class="muted">Add prices to your stock to see this.</div>
        <div v-for="c in valueByCat" :key="c.category" class="row" style="justify-content:space-between;padding:4px 0">
          <span class="chip">{{ c.category }}</span>
          <span class="tnum">{{ fmt(c.value) }}</span>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Price history</h3>
      <p class="muted" style="font-size:.85rem;margin-top:-4px">
        What you typically pay per item — the last price prefills when you re-add it.</p>
      <div v-if="!history.length" class="muted">No priced products yet.</div>
      <table v-else>
        <thead><tr><th>Product</th><th class="num">Typical / unit</th><th class="num">Last paid</th>
          <th style="width:130px">Trend</th></tr></thead>
        <tbody>
          <tr v-for="h in history" :key="h.productId">
            <td>{{ h.name }} <span class="muted" style="font-size:.8rem">· {{ h.category }}</span></td>
            <td class="num tnum">{{ fmtUnit(h.typicalUnitPrice) }}</td>
            <td class="num tnum">{{ fmt(h.lastPrice) }}</td>
            <td>
              <svg v-if="spark(h.points)" width="120" height="26" class="spark">
                <polyline :points="spark(h.points)" fill="none" stroke="var(--accent)" stroke-width="1.5" />
              </svg>
              <span v-else class="muted" style="font-size:.8rem">one buy</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
  <div v-else-if="loading" class="muted" style="padding:24px">Loading spend insights…</div>
  <div v-else class="card" style="padding:32px;text-align:center">
    <p class="muted" style="margin-bottom:12px">
      {{ err || 'No spend data yet — add items with a price to see value and spend here.' }}</p>
    <button class="secondary sm" @click="load()">Retry</button>
  </div>
</template>

<style scoped>
h3 { margin:0 0 6px; }
.spendbars { display:flex; align-items:flex-end; gap:6px; height:150px; margin-top:8px; overflow-x:auto; }
.spendbar { display:flex; flex-direction:column; align-items:center; justify-content:flex-end;
  flex:1 0 26px; min-width:26px; height:100%; }
.spendbar-fill { width:70%; max-width:34px; background:var(--accent); border-radius:4px 4px 0 0;
  transition:height .2s; }
.spendbar-x { font-size:.65rem; color:var(--muted); margin-top:4px; white-space:nowrap; }
.hbar-row { display:grid; grid-template-columns:110px 1fr auto; align-items:center; gap:10px; padding:5px 0; }
.hbar-label { font-size:.85rem; text-transform:capitalize; }
.hbar-track { background:var(--accent-soft); border-radius:6px; height:12px; overflow:hidden; }
.hbar-fill { height:100%; background:var(--accent); border-radius:6px; }
.hbar-val { font-variant-numeric:tabular-nums; font-size:.85rem; min-width:64px; text-align:right; }
.tnum { font-variant-numeric:tabular-nums; }
.num { text-align:right; }
.spark { display:block; }
.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
</style>
