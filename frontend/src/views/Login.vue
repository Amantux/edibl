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
    <form class="card" style="width:380px;max-width:100%" @submit.prevent="submit">
      <div class="brand" style="justify-content:center;padding-bottom:12px"><span class="logo" aria-hidden="true">🥑</span> Edibl</div>
      <p class="muted" style="text-align:center;margin-top:0">Your kitchen's real inventory.</p>
      <label v-if="mode==='register'" class="field"><span>Name</span><input v-model="form.name" autocomplete="name" /></label>
      <label class="field"><span>Email</span><input type="email" required v-model="form.email" autocomplete="email" /></label>
      <label class="field"><span>Password</span><input type="password" required v-model="form.password"
        :autocomplete="mode==='register' ? 'new-password' : 'current-password'" /></label>
      <p v-if="error" role="alert" style="color:var(--danger);font-size:.85rem">{{ error }}</p>
      <button type="submit" style="width:100%" :disabled="busy">
        {{ busy ? 'Please wait…' : (mode==='register' ? 'Create account' : 'Sign in') }}</button>
      <p class="muted" style="text-align:center;margin-bottom:0;font-size:.85rem;margin-top:12px">
        <button type="button" class="linkbtn" @click="mode = mode==='login' ? 'register' : 'login'">
          {{ mode==='login' ? 'Need an account? Register' : 'Have an account? Sign in' }}</button></p>
    </form>
  </div>
</template>
