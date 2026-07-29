<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { ui } from '../ui'
import { useLiveRefresh } from '../live'
const plan = ref(null)
const integ = ref(null)
const raw = ref('')
const busy = ref(false)
const loading = ref(true)
const loadError = ref('')
const cookSug = ref(null)      // {configured, reachable, suggestions[]} — "what can I cook"
const useup = ref(null)        // recipes using soon-expiring stock
const sugLoading = ref(false)
const cookingId = ref('')      // recipe id currently cooking/shopping (disables its buttons)

async function load(silent = false) {
  if (!silent) { loading.value = true; loadError.value = '' }
  try {
    const [p, i] = await Promise.all([api.get('/plan'), api.get('/integrations/status')])
    plan.value = p; integ.value = i
  } catch (e) { if (!silent) loadError.value = e.message || 'Could not load the meal plan.' }
  finally { if (!silent) loading.value = false }
}
async function loadSuggestions() {
  sugLoading.value = true
  try {
    const [m, u] = await Promise.all([
      api.get('/cook/suggestions?mode=make'),
      api.get('/cook/suggestions?mode=useup'),
    ])
    cookSug.value = m; useup.value = u
  } catch (e) { cookSug.value = { configured: true, reachable: false, suggestions: [] } }
  finally { sugLoading.value = false }
}
async function cookRecipe(r) {
  if (!confirm(`Deduct ${r.name}'s ingredients from your stock?`)) return
  cookingId.value = r.recipeId
  // A per-press token so a network-level retry replays server-side instead of
  // deducting twice; a fresh press (deliberate re-cook) gets a new token.
  const idempotencyKey = (crypto.randomUUID?.() || String(Date.now()))
  try {
    const res = await api.post(`/cook/recipe/${r.recipeId}`, { idempotencyKey })
    const used = (res.cooked || []).filter((c) => c.consumed > 0).length
    const short = (res.cooked || []).filter((c) => c.shortfall > 0)
    ui.success(short.length
      ? `Deducted ${used} ingredient(s) for ${r.name}. Short on: ${short.map((s) => s.name).join(', ')}.`
      : `Deducted all ${used} ingredient(s) for ${r.name}.`)
    await Promise.all([load(true), loadSuggestions()])
  } catch (e) { ui.error(e.message || 'Could not cook that recipe.') }
  finally { cookingId.value = '' }
}
async function shopRecipe(r) {
  cookingId.value = r.recipeId
  try {
    const res = await api.post(`/cook/recipe/${r.recipeId}/shop`)
    ui.success(res.added
      ? `Added ${res.added} missing item(s) to the shopping list.`
      : `Nothing missing for ${r.name} — you have it all.`)
  } catch (e) { ui.error(e.message || 'Could not add to the shopping list.') }
  finally { cookingId.value = '' }
}
onMounted(() => { load(); loadSuggestions() })
useLiveRefresh(() => { load(true); loadSuggestions() })   // live sync from chat / other devices

async function ingest() {
  // Parse "2 eggs", "200 g flour", "milk" lines into ingredient objects.
  const items = raw.value.split('\n').map((l) => l.trim()).filter(Boolean).map((line) => {
    const m = line.match(/^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?\s+(.+)$/)
    if (m) return { name: m[3], quantity: parseFloat(m[1]), unit: m[2] || 'count' }
    return { name: line, quantity: 1, unit: 'count' }
  })
  if (!items.length) return
  busy.value = true
  try {
    await api.post('/integrations/mymeal/plan', { meal: 'Manual', items })
    raw.value = ''; ui.success(`Added ${items.length} item(s) to the plan.`); await load()
  } catch (e) { ui.error(e.message || 'Could not add to the plan.') } finally { busy.value = false }
}
async function order() {
  try { const r = await api.post('/plan/order'); ui.success(`Added ${r.added} item(s) to the shopping list.`) }
  catch (e) { ui.error(e.message || 'Could not order.') }
}
async function cook() {
  if (!confirm('Deduct this meal\'s ingredients from your stock?')) return
  try {
    const r = await api.post('/plan/cook', { clear: true })
    const used = r.cooked.filter((c) => c.consumed > 0).length
    const short = r.cooked.filter((c) => c.shortfall > 0)
    ui.success(short.length
      ? `Deducted ${used} ingredient(s). Short on: ${short.map((s) => s.name).join(', ')}.`
      : `Deducted all ${used} ingredient(s) from stock.`)
    await load()
  } catch (e) { ui.error(e.message || 'Could not deduct ingredients.') }
}
async function clearPlan() {
  if (!confirm('Clear the whole meal plan?')) return
  try { await api.post('/plan/clear'); ui.info('Meal plan cleared.'); await load() }
  catch (e) { ui.error(e.message || 'Could not clear the plan.') }
}
async function remove(id) {
  try { await api.del('/plan/' + id); await load() } catch (e) { ui.error(e.message || 'Could not remove.') }
}
</script>

<template>
  <div class="page-head"><h1>🍽️ Meal plan</h1><div class="grow"></div>
    <button v-if="plan?.planned.length" @click="cook">🍳 Made it</button>
    <button v-if="plan?.shortfall.length" class="secondary" @click="order">🛒 Order the {{ plan.shortfall.length }} missing</button>
    <button v-if="plan?.planned.length" class="secondary" @click="clearPlan">Clear</button></div>

  <div v-if="loadError" class="card" style="border-color:var(--danger)">
    <strong style="color:var(--danger)">Couldn't load the meal plan.</strong>
    <span class="muted"> {{ loadError }}</span>
    <button class="secondary sm" style="margin-left:8px" @click="load">Retry</button>
  </div>

  <div class="card" style="background:var(--accent-soft);border-color:var(--accent)">
    <strong>How this works:</strong> <span class="muted">myMeal owns the recipes; Edibl owns the real inventory.
    Planned ingredients (from myMeal or pasted below) are reconciled against what you actually have —
    so you can see the shortfall and order exactly what's missing.</span>
    <div v-if="integ" style="margin-top:8px" class="muted" >
      myMeal integration: <span class="chip">{{ integ.myMeal.configured ? 'connected' : 'not configured' }}</span>
    </div>
  </div>

  <!-- What can I cook right now — myMeal recipes ranked by Edibl stock -->
  <div class="card">
    <div class="row"><h2 style="flex:1;margin:0">🍳 What can I cook right now</h2>
      <button class="ghost sm" :disabled="sugLoading" aria-label="Refresh suggestions"
        @click="loadSuggestions">↻</button></div>
    <div v-if="sugLoading && !cookSug" class="muted" style="margin-top:8px">Ranking recipes against your stock…</div>
    <div v-else-if="cookSug && !cookSug.configured" class="muted" style="margin-top:8px">
      Connect myMeal to see recipes you can make from what's on hand.
      <router-link to="/data">Connect myMeal →</router-link></div>
    <div v-else-if="cookSug && !cookSug.reachable" class="muted" style="margin-top:8px">
      myMeal isn't reachable right now.
      <button class="ghost sm" @click="loadSuggestions">Retry</button></div>
    <div v-else-if="cookSug && !cookSug.suggestions.length" class="empty"><div class="ico">🍳</div>
      <p>No matches yet — add recipes in myMeal and they'll show up ranked by what you have.</p></div>
    <div v-else-if="cookSug" class="cooklist">
      <div v-for="r in cookSug.suggestions" :key="r.recipeId" class="cookrow">
        <div class="cookmain">
          <strong>{{ r.name }}</strong>
          <span class="badge" :class="r.missingCount ? 'expiring' : 'fresh'">{{ r.haveCount }}/{{ r.totalCount }} on hand</span>
          <div class="covbar" :title="`${Math.round((r.coverage||0)*100)}% of ingredients on hand`">
            <span class="cov" :style="{ width: Math.round((r.coverage || 0) * 100) + '%' }"></span></div>
          <div v-if="r.missing && r.missing.length" class="muted sm missing">
            missing: <span v-for="(m, i) in r.missing" :key="i" class="chip warn">{{ m.name || m }}</span></div>
        </div>
        <div class="cookact">
          <button class="tonal sm" :disabled="cookingId === r.recipeId" @click="cookRecipe(r)">🍳 Cook it</button>
          <button v-if="r.missingCount" class="secondary sm" :disabled="cookingId === r.recipeId"
            @click="shopRecipe(r)">🛒 Shop missing</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Use it up — recipes that consume soon-expiring stock -->
  <div v-if="useup && useup.reachable && useup.suggestions.length" class="card">
    <h2 style="margin:0 0 4px">⏳ Use it up before it expires</h2>
    <div class="cooklist">
      <div v-for="r in useup.suggestions" :key="r.recipeId" class="cookrow">
        <div class="cookmain">
          <strong>{{ r.name }}</strong>
          <span v-if="r.soonestDaysLeft != null" class="badge expiring">{{ r.soonestDaysLeft }}d left</span>
          <div v-if="r.uses && r.uses.length" class="muted sm missing">
            uses: <span v-for="(u, i) in r.uses" :key="i" class="chip">{{ u.name || u }}</span></div>
        </div>
        <div class="cookact">
          <button class="tonal sm" :disabled="cookingId === r.recipeId" @click="cookRecipe(r)">🍳 Cook it</button>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Paste planned ingredients</h2>
    <textarea v-model="raw" rows="4" placeholder="2 eggs&#10;200 g flour&#10;1 l whole milk&#10;butter"></textarea>
    <div class="row" style="justify-content:flex-end;margin-top:10px">
      <button :disabled="busy || !raw.trim()" @click="ingest">Add to plan</button></div>
  </div>

  <div v-if="loading && !plan" class="card"><div class="muted">Loading your plan…</div></div>
  <div v-else-if="plan" class="card">
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
            :aria-label="`Remove ${it.name} from plan`" @click="remove(plan.planned[idx].id)">✕</button></td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty"><div class="ico">🍽️</div><p>No meal plan yet. Paste ingredients above, or push them from myMeal.</p></div>
  </div>
</template>

<style scoped>
.cooklist { display: flex; flex-direction: column; gap: 4px; margin-top: 10px; }
.cookrow { display: flex; align-items: flex-start; gap: 12px; padding: 10px 0; border-top: 1px solid var(--border); }
.cookrow:first-child { border-top: none; }
.cookmain { flex: 1; min-width: 0; }
.cookmain strong { margin-right: 8px; }
.cookact { display: flex; gap: 6px; flex: none; flex-wrap: wrap; justify-content: flex-end; }
.missing { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; align-items: baseline; }
.covbar { height: 5px; background: var(--accent-soft); border-radius: 999px; margin-top: 6px; overflow: hidden; max-width: 240px; }
.covbar .cov { display: block; height: 100%; background: var(--accent); border-radius: 999px; transition: width .2s; }
</style>
