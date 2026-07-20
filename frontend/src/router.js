import { createRouter, createWebHashHistory } from 'vue-router'
import Dashboard from './views/Dashboard.vue'
import Stock from './views/Stock.vue'
import Shopping from './views/Shopping.vue'
import Plan from './views/Plan.vue'
import Locations from './views/Locations.vue'
import Login from './views/Login.vue'
import { getToken } from './api'

const routes = [
  { path: '/', component: Dashboard },
  { path: '/stock', component: Stock },
  { path: '/shopping', component: Shopping },
  { path: '/plan', component: Plan },
  { path: '/locations', component: Locations },
  { path: '/login', component: Login, meta: { public: true } },
]
const router = createRouter({ history: createWebHashHistory(), routes })
router.beforeEach((to) => {
  // If auth is enabled and we have no token, the API will 401 and redirect.
  if (!to.meta.public && !getToken()) return true
})
export default router
