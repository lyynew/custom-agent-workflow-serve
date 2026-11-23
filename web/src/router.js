import { createRouter, createWebHistory } from 'vue-router'

import { isAuthenticated } from './store'
import AccessView from './views/AccessView.vue'
import AuditView from './views/AuditView.vue'
import LoginView from './views/LoginView.vue'
import PlaygroundView from './views/PlaygroundView.vue'
import RegisterView from './views/RegisterView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/playground' },
    { path: '/login', name: 'login', component: LoginView, meta: { guestOnly: true } },
    { path: '/register', name: 'register', component: RegisterView, meta: { guestOnly: true } },
    { path: '/playground', name: 'playground', component: PlaygroundView, meta: { requiresAuth: true } },
    { path: '/access', name: 'access', component: AccessView, meta: { requiresAuth: true } },
    { path: '/audit', name: 'audit', component: AuditView, meta: { requiresAuth: true } },
    // 旧链接兼容：/chat?... 还带着 agent/conversation 参数
    { path: '/chat', redirect: (to) => ({ name: 'playground', query: to.query }) },
    { path: '/:pathMatch(.*)*', redirect: '/playground' },
  ],
})

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !isAuthenticated()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && isAuthenticated()) {
    return { name: 'playground' }
  }
  return true
})

export default router
