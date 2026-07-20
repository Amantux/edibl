<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { api } from '../api'

const open = ref(false)
const cfg = ref({ enabled: false, provider: 'rules', model: null })
const msgs = ref([])          // {role, content, actions?}
const input = ref('')
const busy = ref(false)
const body = ref(null)

const suggestions = [
  "What's expiring soon?",
  'Do I have eggs?',
  'What am I wasting?',
  'Add 6 eggs to the shopping list',
]

onMounted(async () => {
  try { cfg.value = await api.get('/assistant/config') } catch (e) { /* leave defaults */ }
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

function toggle() { open.value = !open.value; if (open.value) scrollDown() }
</script>

<template>
  <div class="asst">
    <button class="fab" :class="{ open }" @click="toggle" :title="open ? 'Close assistant' : 'Ask Edibl'">
      <span v-if="!open">💬</span><span v-else>✕</span>
    </button>

    <div v-if="open" class="panel card">
      <div class="phead">
        <strong>🥑 Ask Edibl</strong>
        <span class="tag" :class="cfg.enabled ? 'on' : 'off'">
          {{ cfg.enabled ? cfg.provider : 'built-in' }}</span>
      </div>

      <div ref="body" class="pbody">
        <div v-if="!msgs.length" class="hello">
          <p class="muted">Ask about your kitchen — what you have, what's expiring, what you tend to waste — or tell me what you bought or ate.</p>
          <div class="chips">
            <button v-for="s in suggestions" :key="s" class="chip-btn" @click="send(s)">{{ s }}</button>
          </div>
          <p v-if="!cfg.enabled" class="muted tiny">Built-in assistant. For full natural-language chat &amp; voice, set <code>EDIBL_LLM_PROVIDER</code> (ollama / openai / anthropic).</p>
        </div>
        <div v-for="(m, i) in msgs" :key="i" class="msg" :class="m.role">
          <div class="bubble">{{ m.content }}</div>
          <div v-if="m.actions && m.actions.length" class="acts">
            <span v-for="(a, j) in m.actions" :key="j" class="chip">✓ {{ a.tool.replace(/_/g, ' ') }}</span>
          </div>
        </div>
        <div v-if="busy" class="msg assistant"><div class="bubble typing">…</div></div>
      </div>

      <form class="pfoot" @submit.prevent="send()">
        <input v-model="input" :disabled="busy" placeholder="Message Edibl…" autofocus />
        <button type="submit" :disabled="busy || !input.trim()">Send</button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.asst { position: fixed; right: 20px; bottom: 20px; z-index: 60; }
.fab {
  width: 54px; height: 54px; border-radius: 50%; font-size: 1.4rem; cursor: pointer;
  border: none; color: #fff; background: var(--primary, #2f9e57);
  box-shadow: 0 6px 20px rgba(0,0,0,.22); display: grid; place-items: center;
}
.fab.open { background: var(--surface-raised, #33383f); color: var(--text, #eee); }
.panel {
  position: absolute; right: 0; bottom: 66px; width: min(380px, calc(100vw - 40px));
  height: min(560px, calc(100vh - 120px)); display: flex; flex-direction: column;
  padding: 0; overflow: hidden;
}
.phead { display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px; border-bottom: 1px solid var(--border, #333); }
.tag { font-size: .68rem; padding: 2px 8px; border-radius: 999px; text-transform: lowercase; }
.tag.on { background: rgba(47,158,87,.18); color: var(--primary, #2f9e57); }
.tag.off { background: var(--surface, #2a2e34); color: var(--muted, #999); }
.pbody { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.hello { display: flex; flex-direction: column; gap: 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip-btn {
  border: 1px solid var(--border, #333); background: var(--surface, #2a2e34);
  color: inherit; border-radius: 999px; padding: 6px 10px; font-size: .8rem; cursor: pointer;
}
.chip-btn:hover { border-color: var(--primary, #2f9e57); }
.msg { display: flex; flex-direction: column; gap: 4px; max-width: 90%; }
.msg.user { align-self: flex-end; align-items: flex-end; }
.msg.assistant { align-self: flex-start; }
.bubble { padding: 9px 12px; border-radius: 14px; white-space: pre-wrap; line-height: 1.35; font-size: .9rem; }
.msg.user .bubble { background: var(--primary, #2f9e57); color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .bubble { background: var(--surface, #2a2e34); border-bottom-left-radius: 4px; }
.bubble.typing { letter-spacing: 2px; opacity: .6; }
.acts { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font-size: .68rem; padding: 1px 7px; border-radius: 999px;
  background: rgba(47,158,87,.15); color: var(--primary, #2f9e57); }
.pfoot { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border, #333); }
.pfoot input { flex: 1; }
.tiny { font-size: .72rem; }
.muted { color: var(--muted, #999); }
code { font-size: .78rem; }
</style>
