<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const plan = ref(null)
const integ = ref(null)
const raw = ref('')
const busy = ref(false)

async function load() {
  plan.value = await api.get('/plan')
  integ.value = await api.get('/integrations/status')
}
onMounted(load)

async function ingest() {
  // Parse "2 eggs", "200 g flour", "milk" lines into ingredient objects.
  const items = raw.value.split('\n').map((l) => l.trim()).filter(Boolean).map((line) => {
    const m = line.match(/^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?\s+(.+)$/)
    if (m) return { name: m[3], quantity: parseFloat(m[1]), unit: m[2] || 'count' }
    return { name: line, quantity: 1, unit: 'count' }
  })
  if (!items.length) return
  busy.value = true
  try { await api.post('/integrations/mymeal/plan', { meal: 'Manual', items }); raw.value = ''; await load() }
  finally { busy.value = false }
}
async function order() { const r = await api.post('/plan/order'); alert(`Added ${r.added} item(s) to the shopping list.`); }
async function clearPlan() { if (confirm('Clear the whole meal plan?')) { await api.post('/plan/clear'); await load() } }
async function remove(id) { await api.del('/plan/' + id); await load() }
</script>

<template>
  <div class="page-head"><h1>🍽️ Meal plan</h1><div class="grow"></div>
    <button v-if="plan?.shortfall.length" @click="order">🛒 Order the {{ plan.shortfall.length }} missing</button>
    <button v-if="plan?.planned.length" class="secondary" @click="clearPlan">Clear</button></div>

  <div class="card" style="background:var(--accent-soft);border-color:var(--accent)">
    <strong>How this works:</strong> <span class="muted">myMeal owns the recipes; Edibl owns the real inventory.
    Planned ingredients (from myMeal or pasted below) are reconciled against what you actually have —
    so you can see the shortfall and order exactly what's missing.</span>
    <div v-if="integ" style="margin-top:8px" class="muted" >
      myMeal integration: <span class="chip">{{ integ.myMeal.configured ? 'connected' : 'not configured' }}</span>
    </div>
  </div>

  <div class="card">
    <h2>Paste planned ingredients</h2>
    <textarea v-model="raw" rows="4" placeholder="2 eggs&#10;200 g flour&#10;1 l whole milk&#10;butter"></textarea>
    <div class="row" style="justify-content:flex-end;margin-top:10px">
      <button :disabled="busy || !raw.trim()" @click="ingest">Add to plan</button></div>
  </div>

  <div v-if="plan" class="card">
    <div class="row"><h2 style="flex:1;margin:0">Do I have what I need?</h2>
      <span class="badge" :class="plan.canMakeAll ? 'fresh' : 'expiring'">
        {{ plan.canMakeAll ? 'All covered ✓' : plan.shortfall.length + ' short' }}</span></div>
    <table v-if="plan.items.length" style="margin-top:12px">
      <thead><tr><th>Ingredient</th><th>Need</th><th>On hand</th><th>Status</th><th></th></tr></thead>
      <tbody>
        <tr v-for="(it, idx) in plan.items" :key="idx">
          <td><strong>{{ it.name }}</strong>
            <span v-if="it.expiryConcern" class="badge expiring" style="margin-left:6px">expiring</span></td>
          <td>{{ it.need }} {{ it.unit }}</td>
          <td>{{ it.onHand }}</td>
          <td><span class="badge" :class="it.have ? 'fresh' : 'expired'">
            {{ it.have ? 'have it' : 'short ' + it.shortfall }}</span></td>
          <td style="text-align:right"><button v-if="plan.planned[idx]" class="ghost sm"
            @click="remove(plan.planned[idx].id)">✕</button></td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty"><div class="ico">🍽️</div><p>No meal plan yet. Paste ingredients above, or push them from myMeal.</p></div>
  </div>
</template>
