<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'

// ── Access & keys: mint/list/revoke tokens + connect-link sharing ────────────
const tokens = ref([])
const newTokenName = ref('')
const newTokenScope = ref('full')   // full | rest | mcp — what the key unlocks
const minted = ref(null)            // { token, name } — raw token, shown once
const connectUrl = ref('')          // address other apps use to reach this Edibl
const keysBusy = ref(false)
const keysMsg = ref('')
const SCOPE_LABELS = { full: 'Full access', rest: 'REST API only', mcp: 'MCP only' }
function scopeLabel(s) { return SCOPE_LABELS[s] || SCOPE_LABELS.full }
async function loadTokens() { try { tokens.value = await api.get('/tokens') } catch (e) { /* auth off / optional */ } }
async function mintToken() {
  keysBusy.value = true; keysMsg.value = ''
  try {
    const r = await api.post('/tokens', { name: newTokenName.value || 'Connected app', scope: newTokenScope.value })
    minted.value = { token: r.token, name: r.name, scope: r.scope }
    newTokenName.value = ''
    await loadTokens()
  } catch (e) { keysMsg.value = '⚠️ ' + (e.message || 'could not create token') } finally { keysBusy.value = false }
}
async function revokeToken(id) {
  if (!confirm('Revoke this token? Anything using it loses access.')) return
  try { await api.del('/tokens/' + id); await loadTokens() } catch (e) { keysMsg.value = '⚠️ ' + (e.message || 'revoke failed') }
}
function encodeConnect(app, url, token) {
  return app + '-connect:' + btoa(unescape(encodeURIComponent(JSON.stringify({ app, url, token, v: 1 }))))
}
const ediblConnectLink = computed(() =>
  minted.value ? encodeConnect('edibl', connectUrl.value || (typeof window !== 'undefined' ? window.location.origin : ''), minted.value.token) : '')
async function copyText(text, label) {
  try { await navigator.clipboard.writeText(text); keysMsg.value = `✓ ${label} copied.` }
  catch (e) { keysMsg.value = 'Copy failed — select the text and copy manually.' }
}

onMounted(() => {
  loadTokens()
  if (typeof window !== 'undefined') connectUrl.value = window.location.origin
})
</script>

<template>
  <div class="page-head"><h1>🏠 Home Assistant</h1></div>

  <div class="card">
    <h2>Connect Edibl to Home Assistant</h2>
    <p class="muted" style="margin-top:0">Run Edibl as a Home Assistant add-on and it's reachable over Ingress with
      no key — the companion integration and the bundled <strong>MCP</strong> server connect on the internal network.
      You only need a key below when Edibl runs <strong>standalone</strong>, is reached across the network, or has auth on.
      Assist &amp; other MCP clients (e.g. HA's MCP integration pointing at Edibl's <code>/sse</code> endpoint) use an
      <strong>MCP</strong> or Full key as their bearer token.</p>
  </div>

  <div class="card">
    <h2>🔑 Access &amp; keys</h2>
    <p class="muted" style="margin-top:0">Long-lived keys for Home Assistant, an MCP client, or another app to reach Edibl.
      Choose what each key unlocks, generate it, then hand it over with a <strong>connect link</strong>. Revoking a key
      cuts that access instantly.</p>

    <label class="field"><span>Address other apps use to reach Edibl</span>
      <input v-model="connectUrl" placeholder="https://edibl.example.com" /></label>
    <div class="row" style="align-items:flex-end;gap:8px">
      <label class="field" style="flex:1"><span>New key name (what's it for?)</span>
        <input v-model="newTokenName" placeholder="e.g. HA MCP, myMeal" @keyup.enter="mintToken" /></label>
      <label class="field" style="width:220px"><span>Access</span>
        <select v-model="newTokenScope">
          <option value="full">Full access (API + MCP)</option>
          <option value="rest">REST API only</option>
          <option value="mcp">MCP only</option>
        </select></label>
      <button :disabled="keysBusy" @click="mintToken" style="height:38px">Generate</button>
    </div>
    <p class="muted" style="font-size:.78rem;margin:6px 0 0">The MCP endpoint requires a key when Edibl runs with auth on
      (<code>disable_auth: false</code>), when a server token is set, or once you mint an <strong>MCP</strong>-scoped key —
      otherwise it stays open on the internal network. Minting a Full key alone (in open mode) does <em>not</em> lock it.</p>

    <div v-if="minted" style="border:1px solid var(--primary,#2f9e57);border-radius:8px;padding:10px 12px;margin-top:10px;background:rgba(47,158,87,.10)">
      <p style="margin:0 0 6px"><strong>New key “{{ minted.name }}”</strong> <span class="chip">{{ scopeLabel(minted.scope) }}</span> — copy it now, it won't be shown again.</p>
      <code style="display:block;word-break:break-all;background:var(--surface-raised,#f6f6f6);padding:6px 8px;border-radius:6px;font-size:.8rem">{{ minted.token }}</code>
      <div class="row wrap" style="gap:8px;margin-top:8px">
        <button class="secondary sm" @click="copyText(minted.token, 'Token')">Copy token</button>
        <button class="sm" @click="copyText(ediblConnectLink, 'Connect link')">🔗 Copy connect link</button>
        <button class="ghost sm" @click="minted=null">Done</button>
      </div>
      <p class="muted" style="font-size:.78rem;margin:8px 0 0">Paste the connect link into the other app's Edibl connection (fills its URL + token in one step).</p>
    </div>

    <table v-if="tokens.length" style="width:100%;margin-top:12px;font-size:.9rem">
      <thead><tr><th style="text-align:left">Name</th><th style="text-align:left">Access</th><th style="text-align:left">Hint</th><th></th></tr></thead>
      <tbody>
        <tr v-for="t in tokens" :key="t.id">
          <td>{{ t.name }}</td>
          <td><span class="chip">{{ scopeLabel(t.scope) }}</span></td>
          <td class="muted"><code>{{ t.hint }}…</code></td>
          <td style="text-align:right"><button class="ghost sm" style="color:var(--danger)" @click="revokeToken(t.id)">Revoke</button></td>
        </tr>
      </tbody>
    </table>
    <p v-if="keysMsg" class="muted" style="font-size:.85rem;margin-top:8px">{{ keysMsg }}</p>
  </div>
</template>

<style scoped>
.card .field input:not([type="checkbox"]):not([type="file"]),
.card .field select { max-width: 520px; }
.card label.field > span { color: var(--text); font-size: .84rem; }
</style>
