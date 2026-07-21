import { createRouter, createWebHashHistory } from 'vue-router'
import Dashboard from './views/Dashboard.vue'
import Stock from './views/Stock.vue'
import Shopping from './views/Shopping.vue'
import Plan from './views/Plan.vue'
import Locations from './views/Locations.vue'
import Settings from './views/Data.vue'
import Login from './views/Login.vue'
import { getToken } from './api'
import { ensureMe } from './me'

const routes = [
  { path: '/', component: Dashboard },
  { path: '/stock', component: Stock },
  { path: '/shopping', component: Shopping },
  { path: '/plan', component: Plan },
  { path: '/locations', component: Locations },
  { path: '/settings', component: Settings, meta: { ownerOnly: true } },
  { path: '/data', redirect: '/settings' },
  { path: '/login', component: Login, meta: { public: true } },
]
const router = createRouter({ history: createWebHashHistory(), routes })
router.beforeEach(async (to) => {
  // If auth is enabled and we have no token, the API will 401 and redirect.
  if (!to.meta.public && !getToken()) return true
  // Owner-only pages: members are bounced to the dashboard (the server 403s too).
  if (to.meta.ownerOnly) {
    const m = await ensureMe()
    if (!m?.isOwner) return '/'
  }
})
export default router
