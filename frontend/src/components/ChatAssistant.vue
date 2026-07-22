<script setup>
import { ref, nextTick, onMounted, watch } from 'vue'
import { api } from '../api'
import { chatOpen, chatPrefill } from '../chat'

const open = ref(false)
const cfg = ref({ enabled: false, provider: 'rules', model: null })
const msgs = ref([])          // {role, content, actions?}
const input = ref('')
const busy = ref(false)
const body = ref(null)
const inputEl = ref(null)

const suggestions = [
  "What's expiring soon?",
  'Do I have milk?',
  'Add 2 L of organic milk',
  'What should I reorder?',
  'What am I wasting?',
]

async function refreshCfg() { try { cfg.value = await api.get('/assistant/config') } catch (e) { /* keep */ } }
onMounted(refreshCfg)

// Open instantly and refresh config in the background — no wait on toggle.
function doOpen() {
  open.value = true
  refreshCfg(); scrollDown()
  nextTick(() => inputEl.value?.focus())
}
function toggle() { open.value ? (open.value = false) : doOpen() }

// Omnibox / other components can pop the panel open with a prefilled question.
watch(chatOpen, (v) => { if (v) { doOpen(); chatOpen.value = false } })
watch(chatPrefill, (v) => {
  if (!v) return
  input.value = v; chatPrefill.value = ''
  if (open.value) nextTick(() => inputEl.value?.focus())
})

async function scrollDown() {
  await nextTick()
  if (body.value) body.value.scrollTop = body.value.scrollHeight
}

async function send(text) {
  const content = (text ?? input.value).trim()
  if (!content || busy.value) return
  input.value = ''
  msgs.value.push({ role: 'user', content })
  busy.value = true
  await scrollDown()
  try {
    const payload = msgs.value.map((m) => ({ role: m.role, content: m.content }))
    const res = await api.post('/assistant/chat', { messages: payload })
    msgs.value.push({ role: 'assistant', content: res.reply, actions: res.actions || [] })
  } catch (e) {
    msgs.value.push({ role: 'assistant', content: '⚠️ ' + (e.message || 'Something went wrong.') })
  } finally {
    busy.value = false
    await scrollDown()
  }
}

async function undoAction(a) {
  if (!a.undo || a.undone || a.undoing) return
  a.undoing = true
  try {
    await api.post('/assistant/undo', { undo: a.undo })
    a.undone = true
  } catch (e) {
    msgs.value.push({ role: 'assistant', content: '⚠️ Undo failed: ' + (e.message || 'error') })
    await scrollDown()
  } finally { a.undoing = false }
}
</script>

<template>
  <div class="asst">
    <button class="fab" :class="{ open }" @click="toggle" :title="open ? 'Close assistant' : 'Ask Edibl'">
      <span v-if="!open">💬</span><span v-else>✕</span>
    </button>

    <div v-if="open" class="panel card">
      <div class="phead">
        <strong>🥑 Ask Edibl</strong>
        <div class="ph-right">
          <span class="tag" :class="cfg.enabled ? 'on' : 'off'">
            {{ cfg.enabled ? cfg.provider : 'not set up' }}</span>
          <button class="pclose" @click="open = false" aria-label="Close chat">✕</button>
        </div>
      </div>

      <div ref="body" class="pbody">
        <div v-if="!cfg.enabled" class="hello">
          <p class="muted">🔌 The assistant needs an LLM. Set a provider in the Edibl add-on options (or <code>EDIBL_LLM_PROVIDER</code>):</p>
          <ul class="setup">
            <li><strong>Ollama</strong> (local): base URL <code>http://homeassistant.local:11434</code>, model <code>llama3.1</code></li>
            <li><strong>OpenAI</strong>: API key + model <code>gpt-4o-mini</code></li>
            <li><strong>Anthropic</strong>: API key + model <code>claude-opus-4-8</code></li>
          </ul>
          <p class="muted tiny">Then it can look things up and add / update / remove stock and your shopping list by chat.</p>
        </div>
        <div v-else-if="!msgs.length" class="hello">
          <p class="muted">Ask about your kitchen — what you have, what's expiring, what you tend to waste — or tell me what you bought, ate, or want to change.</p>
          <div class="chips">
            <button v-for="s in suggestions" :key="s" class="chip-btn" @click="send(s)">{{ s }}</button>
          </div>
        </div>
        <div v-for="(m, i) in msgs" :key="i" class="msg" :class="m.role">
          <div class="bubble">{{ m.content }}</div>
          <div v-if="m.actions && m.actions.length" class="acts">
            <div v-for="(a, j) in m.actions" :key="j" class="act" :class="{ mut: a.undoable, done: a.undone }">
              <span class="tick">{{ a.undone ? '↩' : (a.undoable ? '✎' : '🔍') }}</span>
              <span class="lbl">{{ a.label || a.tool.replace(/_/g, ' ') }}</span>
              <button v-if="a.undoable && !a.undone" class="undo-btn" :disabled="a.undoing" @click="undoAction(a)">
                {{ a.undoing ? '…' : 'Undo' }}</button>
              <span v-else-if="a.undone" class="undone-tag">undone</span>
            </div>
          </div>
        </div>
        <div v-if="busy" class="msg assistant"><div class="bubble typing"><span></span><span></span><span></span></div></div>
      </div>

      <form class="pfoot" @submit.prevent="send()">
        <input ref="inputEl" v-model="input" :disabled="busy || !cfg.enabled"
          :placeholder="cfg.enabled ? 'Message Edibl…' : 'Configure an LLM provider to chat'" />
        <button type="submit" :disabled="busy || !cfg.enabled || !input.trim()" aria-label="Send">➤</button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.asst { position: fixed; right: 20px; bottom: 20px; z-index: 60; }
.fab {
  width: 56px; height: 56px; border-radius: 50%; font-size: 1.4rem; cursor: pointer;
  border: none; color: #fff; background: linear-gradient(135deg, var(--accent), #94d82d);
  box-shadow: 0 6px 20px rgba(20,40,25,.28); display: grid; place-items: center;
  transition: transform .14s ease, box-shadow .14s ease;
}
.fab:hover { transform: translateY(-2px) scale(1.03); box-shadow: 0 10px 28px rgba(20,40,25,.32); }
.fab.open { background: var(--surface-2); color: var(--text); }
.panel {
  position: absolute; right: 0; bottom: 68px; width: min(390px, calc(100vw - 40px));
  height: min(580px, calc(100vh - 120px)); display: flex; flex-direction: column;
  padding: 0; overflow: hidden; box-shadow: var(--popover-shadow);
  transform-origin: bottom right; animation: pop .16s ease;
}
@keyframes pop { from { transform: translateY(10px) scale(.98); opacity: 0; } to { transform: none; opacity: 1; } }
.phead { display: flex; align-items: center; justify-content: space-between;
  padding: 13px 15px; border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, var(--accent-soft), transparent); }
.ph-right { display: flex; align-items: center; gap: 8px; }
.pclose { background: transparent; color: var(--muted); border: none; padding: 4px 8px;
  font-size: 1rem; line-height: 1; border-radius: 8px; cursor: pointer; }
.pclose:hover { background: var(--surface-2); color: var(--text); }
.tag { font-size: .68rem; padding: 2px 9px; border-radius: 999px; text-transform: lowercase; font-weight: 600; }
.tag.on { background: rgba(47,158,68,.18); color: var(--accent); }
.tag.off { background: var(--surface-2); color: var(--muted); }
.pbody { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.hello { display: flex; flex-direction: column; gap: 10px; }
.setup { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; font-size: .82rem; }
.setup code { font-size: .76rem; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip-btn {
  border: 1px solid var(--border); background: var(--surface-2);
  color: inherit; border-radius: 999px; padding: 6px 11px; font-size: .8rem; cursor: pointer;
  transition: border-color .12s ease, transform .12s ease;
}
.chip-btn:hover { border-color: var(--accent); transform: translateY(-1px); }
.msg { display: flex; flex-direction: column; gap: 4px; max-width: 92%; animation: rise .16s ease; }
@keyframes rise { from { transform: translateY(6px); opacity: 0; } to { transform: none; opacity: 1; } }
.msg.user { align-self: flex-end; align-items: flex-end; }
.msg.assistant { align-self: flex-start; }
.bubble { padding: 9px 13px; border-radius: 15px; white-space: pre-wrap; line-height: 1.4; font-size: .9rem; }
.msg.user .bubble { background: var(--accent); color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .bubble { background: var(--surface-2); border-bottom-left-radius: 4px; }
.bubble.typing { display: inline-flex; gap: 4px; align-items: center; }
.bubble.typing span { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); opacity: .5;
  animation: blink 1.2s infinite ease-in-out; }
.bubble.typing span:nth-child(2) { animation-delay: .2s; } .bubble.typing span:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: .9; } }
.acts { display: flex; flex-direction: column; gap: 4px; margin-top: 2px; }
.act { display: flex; align-items: center; gap: 6px; font-size: .72rem;
  padding: 4px 9px; border-radius: 9px; background: var(--surface-2); }
.act.mut { background: rgba(47,158,68,.12); }
.act.done { opacity: .55; }
.act .tick { flex: none; }
.act .lbl { flex: 1; color: var(--text); }
.act.done .lbl { text-decoration: line-through; }
.undo-btn { flex: none; border: 1px solid var(--accent); background: transparent;
  color: var(--accent); border-radius: 6px; padding: 1px 8px; font-size: .7rem; cursor: pointer; }
.undo-btn:hover { background: rgba(47,158,68,.15); }
.undone-tag { flex: none; font-size: .68rem; color: var(--muted); }
.pfoot { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border); }
.pfoot input { flex: 1; border-radius: 999px; }
.pfoot button { border-radius: 50%; width: 38px; height: 38px; padding: 0; flex: none; }
.tiny { font-size: .72rem; }
.muted { color: var(--muted); }
code { font-size: .78rem; }

@media (max-width: 560px) {
  .asst { right: 14px; bottom: 14px; }
  .panel { position: fixed; right: 0; left: 0; bottom: 0; width: 100vw;
    height: 82vh; border-radius: 18px 18px 0 0; animation: sheet .18s ease; }
  @keyframes sheet { from { transform: translateY(30px); opacity: .6; } to { transform: none; opacity: 1; } }
}
@media (prefers-reduced-motion: reduce) {
  .fab, .panel, .msg, .chip-btn, .bubble.typing span { animation: none !important; transition: none !important; }
}
</style>
