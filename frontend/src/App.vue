<script setup>
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import ChatAssistant from './components/ChatAssistant.vue'
const route = useRoute()
const menuOpen = ref(false)
const nav = [
  { to: '/', icon: '📊', label: 'Dashboard' },
  { to: '/stock', icon: '🥫', label: 'Stock' },
  { to: '/plan', icon: '🍽️', label: 'Meal plan' },
  { to: '/shopping', icon: '🛒', label: 'Shopping' },
  { to: '/locations', icon: '📍', label: 'Locations' },
  { to: '/settings', icon: '⚙️', label: 'Settings' },
]
watch(() => route.path, () => { menuOpen.value = false })
</script>

<template>
  <template v-if="route.meta.public">
    <router-view />
  </template>
  <div v-else class="app-shell">
    <!-- Mobile top bar -->
    <header class="topbar">
      <button class="menu-btn" aria-label="Menu" @click="menuOpen = !menuOpen">☰</button>
      <div class="brand"><span class="logo">🥑</span> Edibl</div>
    </header>

    <div v-if="menuOpen" class="nav-backdrop" @click="menuOpen = false"></div>
    <aside class="sidebar" :class="{ open: menuOpen }">
      <div class="brand"><span class="logo">🥑</span> Edibl</div>
      <router-link v-for="n in nav" :key="n.to" :to="n.to" class="nav-link">
        <span class="ico">{{ n.icon }}</span> {{ n.label }}
      </router-link>
      <div class="grow"></div>
      <div class="muted" style="padding:8px 11px;font-size:.78rem">Your kitchen's real inventory</div>
    </aside>
    <div class="main"><div class="content"><router-view /></div></div>
    <ChatAssistant />
  </div>
</template>
