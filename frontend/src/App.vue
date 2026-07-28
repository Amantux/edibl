<script setup>
import { ref, watch, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { me, ensureMe } from './me'
import ChatAssistant from './components/ChatAssistant.vue'
import Toaster from './components/Toaster.vue'
import ReportBug from './components/ReportBug.vue'
const route = useRoute()
const menuOpen = ref(false)
const showReport = ref(false)
onMounted(ensureMe)
const NAV = [
  { to: '/', icon: '📊', label: 'Dashboard' },
  { to: '/stock', icon: '🥫', label: 'Stock' },
  { to: '/plan', icon: '🍽️', label: 'Meal plan' },
  { to: '/shopping', icon: '🛒', label: 'Shopping' },
  { to: '/locations', icon: '📍', label: 'Locations' },
  { to: '/settings', icon: '⚙️', label: 'Settings', ownerOnly: true, util: true },
  { to: '/review', icon: '🗂️', label: 'Review', util: true },
  { to: '/home-assistant', icon: '🏠', label: 'Home Assistant', ownerOnly: true, util: true },
  { to: '/database', icon: '🗄️', label: 'Database', ownerOnly: true, util: true },
]
// Owner-only items appear only once we've confirmed the user is the owner (the
// server enforces regardless — this just hides what members can't use).
const nav = computed(() => NAV.filter(n => !n.ownerOnly || me.value?.isOwner === true))
// Split into the primary workflow + a "Utilities" group (config/admin), like myMeal.
const mainNav = computed(() => nav.value.filter(n => !n.util))
const utilNav = computed(() => nav.value.filter(n => n.util))
watch(() => route.path, () => { menuOpen.value = false })
</script>

<template>
  <template v-if="route.meta.public">
    <router-view />
  </template>
  <div v-else class="app-shell">
    <a href="#main" class="skip-link">Skip to content</a>
    <!-- Mobile top bar -->
    <header class="topbar">
      <button class="menu-btn" :aria-expanded="menuOpen" aria-label="Menu"
        @click="menuOpen = !menuOpen">☰</button>
      <div class="brand"><span class="logo" aria-hidden="true">🥑</span> Edibl</div>
      <button class="menu-btn" style="margin-left:auto" title="Report a bug"
        aria-label="Report a bug" @click="showReport = true">🐛</button>
    </header>

    <div v-if="menuOpen" class="nav-backdrop" @click="menuOpen = false"></div>
    <aside class="sidebar" :class="{ open: menuOpen }">
      <div class="brand"><span class="logo" aria-hidden="true">🥑</span> Edibl</div>
      <nav aria-label="Primary">
        <router-link v-for="n in mainNav" :key="n.to" :to="n.to" class="nav-link">
          <span class="ico" aria-hidden="true">{{ n.icon }}</span> {{ n.label }}
        </router-link>
        <div v-if="utilNav.length" class="section-label">Utilities</div>
        <router-link v-for="n in utilNav" :key="n.to" :to="n.to" class="nav-link">
          <span class="ico" aria-hidden="true">{{ n.icon }}</span> {{ n.label }}
        </router-link>
        <a class="nav-link" href="#" @click.prevent="showReport = true">
          <span class="ico" aria-hidden="true">🐛</span> Report a bug
        </a>
      </nav>
      <div class="grow"></div>
      <div class="muted" style="padding:8px 11px;font-size:.78rem">Your kitchen's real inventory</div>
    </aside>
    <main id="main" class="main"><div class="content"><router-view /></div></main>
    <ChatAssistant />
    <ReportBug v-if="showReport" @close="showReport = false" />
    <Toaster />
  </div>
</template>
