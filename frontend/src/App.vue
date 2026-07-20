<script setup>
import { useRoute } from 'vue-router'
import ChatAssistant from './components/ChatAssistant.vue'
const route = useRoute()
const nav = [
  { to: '/', icon: '📊', label: 'Dashboard' },
  { to: '/stock', icon: '🥫', label: 'Stock' },
  { to: '/plan', icon: '🍽️', label: 'Meal plan' },
  { to: '/shopping', icon: '🛒', label: 'Shopping' },
  { to: '/locations', icon: '📍', label: 'Locations' },
  { to: '/data', icon: '💾', label: 'Data' },
]
</script>

<template>
  <template v-if="route.meta.public">
    <router-view />
  </template>
  <div v-else class="app-shell">
    <aside class="sidebar">
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
