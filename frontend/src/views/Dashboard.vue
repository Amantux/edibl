<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { ui } from '../ui'
import { useLiveRefresh } from '../live'
import AddStockModal from '../components/AddStockModal.vue'
const router = useRouter()
const d = ref(null)
const runout = ref([])
const lifecycle = ref([])
const reorder = ref([])
const showAdd = ref(false)

async function loadDash(silent = false) {
  try {
    // Parallel — the landing page shouldn't wait on four round-trips in series.
    const [dash, ro, lc, re] = await Promise.all([
      api.get('/dashboard'), api.get('/dashboard/runout'),
      api.get('/dashboard/lifecycle'), api.get('/shopping/reorder'),
    ])
    d.value = dash; runout.value = ro.items
    lifecycle.value = lc.items; reorder.value = re.suggestions || []
  } catch (e) { if (!silent) ui.error(e.message || 'Could not load the dashboard.') }
}
onMounted(() => loadDash())
// Live sync from chat / other devices (silent so background refreshes don't toast).
useLiveRefresh(() => loadDash(true))

// One-line "here's what needs attention" summary under the title.
const attention = computed(() => {
  if (!d.value) return ''
  const bits = []
  if (d.value.totals.expiring) bits.push(`${d.value.totals.expiring} expiring`)
  if (d.value.totals.expired) bits.push(`${d.value.totals.expired} expired`)
  if (reorder.value.length) bits.push(`${reorder.value.length} to restock`)
  if (d.value.totals.open) bits.push(`${d.value.totals.open} open`)
  return bits.length ? bits.join(' · ') : 'Everything looks in good shape. 🥑'
})

function go(path) { router.push(path) }
async function addToList(sug) {
  try {
    await api.post('/shopping', { name: sug.name, quantity: sug.suggestedQuantity,
      unit: sug.unit, source: 'low_stock', productId: sug.productId, note: 'Running low' })
    reorder.value = reorder.value.filter((s) => s.productId !== sug.productId)
    ui.success(`Added ${sug.name} to the shopping list (low).`)
  } catch (e) { ui.error(e.message || 'Could not add to list.') }
}
</script>

<template>
  <div class="page-head"><h1>Kitchen</h1>
    <span v-if="d" class="muted" style="font-size:.9rem">· {{ attention }}</span>
    <div class="grow"></div>
    <button @click="showAdd = true">＋ Add stock</button></div>

  <AddStockModal v-model="showAdd" @added="loadDash" />

  <div v-if="d">
    <!-- Needs attention: tap through to the matching stock view -->
    <div class="smart-grid">
      <button class="smart bad" @click="go('/stock?focus=expiring')">
        <div class="st-top"><span class="st-n">{{ d.totals.expiring + d.totals.expired }}</span><span>⏰</span></div>
        <div class="st-title">Use it or lose it</div>
        <div class="st-peek">{{ (d.expiring[0] && d.expiring.slice(0,3).map(s=>s.product?.name).join(' · ')) || 'Nothing expiring soon' }}</div>
      </button>
      <button class="smart" @click="go('/stock?focus=open')">
        <div class="st-top"><span class="st-n">{{ d.totals.open }}</span><span>📭</span></div>
        <div class="st-title">Open packages</div>
        <div class="st-peek">Opened items — use these first.</div>
      </button>
      <button class="smart warn" @click="go('/stock?focus=low')">
        <div class="st-top"><span class="st-n">{{ reorder.length }}</span><span>🛒</span></div>
        <div class="st-title">Restock</div>
        <div class="st-peek">{{ reorder.slice(0,3).map(s=>s.name).join(' · ') || 'Set reorder levels on items' }}</div>
      </button>
      <button class="smart" @click="go('/locations')">
        <div class="st-top"><span class="st-n">✓</span><span>📋</span></div>
        <div class="st-title">Reconcile a place</div>
        <div class="st-peek">Walk a location and fix what's really there.</div>
      </button>
    </div>

    <!-- At a glance -->
    <div class="stat-grid" style="margin-bottom:18px">
      <button class="stat as-btn" @click="go('/stock')"><div class="value">{{ d.totals.lots }}</div><div class="label">items in stock →</div></button>
      <div class="stat"><div class="value">{{ d.totals.products }}</div><div class="label">products</div></div>
      <button class="stat as-btn" @click="go('/locations')"><div class="value">{{ d.totals.locations }}</div><div class="label">locations →</div></button>
      <div class="stat"><div class="value">{{ d.totals.expired }}</div><div class="label" :style="d.totals.expired ? 'color:var(--danger)' : ''">expired</div></div>
      <div v-if="d.totals.value" class="stat"><div class="value">${{ d.totals.value }}</div><div class="label">est. value</div></div>
    </div>

    <div class="card">
      <div class="row"><h2 style="margin:0">⏰ Use it or lose it</h2><div class="grow"></div>
        <button v-if="d.expiring.length" class="ghost sm" @click="go('/stock?focus=expiring')">See all →</button></div>
      <table v-if="d.expiring.length" style="margin-top:10px">
        <thead><tr><th>Item</th><th>Where</th><th>Qty</th><th>Expires</th></tr></thead>
        <tbody>
          <tr v-for="s in d.expiring.slice(0,6)" :key="s.id" class="clickable" @click="go('/stock?focus=expiring')">
            <td><strong>{{ s.product?.name }}</strong></td>
            <td class="muted">{{ s.location?.name || '—' }}</td>
            <td>{{ s.quantityKind === 'exact' ? (s.quantity + ' ' + s.unit) : s.quantityText }}</td>
            <td><span class="badge" :class="s.expiryStatus">
              {{ s.daysToExpiry < 0 ? 'expired' : s.daysToExpiry + 'd' }}</span></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted" style="margin-top:8px">Nothing expiring soon — nicely stocked. 🥬</div>
    </div>

    <div class="card">
      <h2>🛒 Restock now</h2>
      <p class="muted" style="margin-top:2px">Items below their reorder level (reserved stock accounted for).</p>
      <table v-if="reorder.length" style="margin-top:6px">
        <thead><tr><th>Item</th><th>Available</th><th>Suggested</th><th></th></tr></thead>
        <tbody>
          <tr v-for="s in reorder" :key="s.productId">
            <td><strong>{{ s.name }}</strong> <span v-if="s.staple" class="chip">staple</span></td>
            <td>{{ s.available }} {{ s.unit }}<span v-if="s.reserved" class="muted"> · {{ s.reserved }} reserved</span></td>
            <td>{{ s.suggestedQuantity }} {{ s.unit }}</td>
            <td style="text-align:right"><button class="sm" @click="addToList(s)">Add to list</button></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted" style="margin-top:6px">Nothing to restock. Set a minimum on an item (its ⋯ menu on the Stock page) and it'll show up here.</div>
    </div>

    <div class="card">
      <h2>📉 Running low (predicted)</h2>
      <table v-if="runout.length">
        <thead><tr><th>Product</th><th>On hand</th><th>Runs out in</th></tr></thead>
        <tbody>
          <tr v-for="r in runout" :key="r.product.id">
            <td><strong>{{ r.product.name }}</strong></td>
            <td>{{ r.onHand }}</td>
            <td><span class="badge" :class="r.daysLeft <= 3 ? 'expiring' : ''">~{{ r.daysLeft }} days</span></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted">Not enough consumption history yet to forecast runout.</div>
    </div>

    <div class="card">
      <h2>♻️ Lifecycle &amp; waste (learned)</h2>
      <table v-if="lifecycle.length">
        <thead><tr><th>Product</th><th>Lost</th><th>Suggestion</th></tr></thead>
        <tbody>
          <tr v-for="r in lifecycle" :key="r.productId">
            <td><strong>{{ r.productName }}</strong></td>
            <td><span class="badge" :class="r.wasteRate >= 0.4 ? 'expiring' : ''">{{ r.wasted }}×</span></td>
            <td class="muted">{{ r.suggestion }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted">No waste recorded yet — Edibl learns your food's real shelf life as you mark what you eat vs. toss. 🌱</div>
    </div>
  </div>
  <div v-else class="empty"><div class="ico">🥑</div><p>Loading your kitchen…</p></div>
</template>

<style scoped>
.stat.as-btn { text-align: left; cursor: pointer; color: var(--text); font-weight: 400;
  transition: border-color .12s ease, transform .12s ease; }
.stat.as-btn:hover { border-color: var(--accent); transform: translateY(-1px); background: var(--surface); }
.stat .value { color: var(--text); }
</style>
