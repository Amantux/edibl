<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, setToken } from '../api'
const router = useRouter()
const mode = ref('login')
const form = ref({ name: '', email: '', password: '' })
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''; busy.value = true
  try {
    if (mode.value === 'register') {
      await api.post('/users/register', form.value)
    }
    const res = await api.post('/users/login', { email: form.value.email, password: form.value.password })
    setToken(res.token)
    router.push('/')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <div style="display:grid;place-items:center;min-height:100vh;padding:20px">
    <div class="card" style="width:380px;max-width:100%">
      <div class="brand" style="justify-content:center;padding-bottom:12px"><span class="logo">🥑</span> Edibl</div>
      <p class="muted" style="text-align:center;margin-top:0">Your kitchen's real inventory.</p>
      <label v-if="mode==='register'" class="field"><span>Name</span><input v-model="form.name" /></label>
      <label class="field"><span>Email</span><input type="email" v-model="form.email" autocomplete="email" /></label>
      <label class="field"><span>Password</span><input type="password" v-model="form.password"
        :autocomplete="mode==='register' ? 'new-password' : 'current-password'" @keyup.enter="submit" /></label>
      <p v-if="error" style="color:var(--danger);font-size:.85rem">{{ error }}</p>
      <button style="width:100%" :disabled="busy" @click="submit">{{ mode==='register' ? 'Create account' : 'Sign in' }}</button>
      <p class="muted" style="text-align:center;margin-bottom:0;font-size:.85rem;margin-top:12px">
        <a href="#" @click.prevent="mode = mode==='login' ? 'register' : 'login'">
          {{ mode==='login' ? 'Need an account? Register' : 'Have an account? Sign in' }}</a></p>
    </div>
  </div>
</template>
