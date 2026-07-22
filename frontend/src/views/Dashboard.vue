<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
const router = useRouter()
const d = ref(null)
const runout = ref([])
const lifecycle = ref([])
onMounted(async () => {
  try {
    d.value = await api.get('/dashboard')
    runout.value = (await api.get('/dashboard/runout')).items
    lifecycle.value = (await api.get('/dashboard/lifecycle')).items
  } catch (e) { /* handled by api 401 redirect */ }
})
</script>

<template>
  <div class="page-head"><h1>Dashboard</h1><div class="grow"></div>
    <button @click="router.push('/stock?add=1')">＋ Add stock</button></div>

  <div v-if="d">
    <div class="stat-grid" style="margin-bottom:18px">
      <div class="stat"><div class="value">{{ d.totals.lots }}</div><div class="label">items in stock</div></div>
      <div class="stat"><div class="value" style="color:var(--warning)">{{ d.totals.expiring }}</div><div class="label">expiring soon</div></div>
      <div class="stat"><div class="value" style="color:var(--danger)">{{ d.totals.expired }}</div><div class="label">expired</div></div>
      <div class="stat"><div class="value">{{ d.totals.products }}</div><div class="label">products</div></div>
      <div class="stat"><div class="value">{{ d.totals.locations }}</div><div class="label">locations</div></div>
    </div>

    <div class="card">
      <h2>⏰ Use it or lose it</h2>
      <table v-if="d.expiring.length">
        <thead><tr><th>Item</th><th>Where</th><th>Qty</th><th>Expires</th></tr></thead>
        <tbody>
          <tr v-for="s in d.expiring" :key="s.id">
            <td><strong>{{ s.product?.name }}</strong></td>
            <td class="muted">{{ s.location?.name || '—' }}</td>
            <td>{{ s.quantity }} {{ s.unit }}</td>
            <td><span class="badge" :class="s.expiryStatus">
              {{ s.daysToExpiry < 0 ? 'expired' : s.daysToExpiry + 'd' }}</span></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted">Nothing expiring soon — nicely stocked. 🥬</div>
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
