<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { auth, clearAuth } from '../store'

const route = useRoute()
const router = useRouter()

const nav = [
  { name: 'playground', label: '测试台', hint: '对话与工具' },
  { name: 'access', label: 'Token 接入', hint: '验签与试跑' },
  { name: 'audit', label: '审计日志', hint: '请求流水' },
]

const current = computed(() => route.name)

function logout() {
  clearAuth()
  router.push('/login')
}
</script>

<template>
  <div class="app">
    <nav class="rail">
      <div class="rail-head">
        <span class="brand-mark">耘</span>
        <div>
          <div class="rail-title">AI 服务控制台</div>
          <div class="rail-sub">测试 · 接入 · 审计</div>
        </div>
      </div>

      <div class="rail-nav">
        <RouterLink
          v-for="item in nav"
          :key="item.name"
          :to="{ name: item.name }"
          class="rail-item"
          :class="{ active: current === item.name }"
        >
          <span class="rail-item-label">{{ item.label }}</span>
          <span class="rail-item-hint">{{ item.hint }}</span>
        </RouterLink>
      </div>

      <div class="rail-foot">
        <div class="rail-user">
          <span class="avatar">{{ (auth.user?.username || '?').slice(0, 1).toUpperCase() }}</span>
          <div class="rail-user-text">
            <div class="rail-user-name">{{ auth.user?.username || '未知用户' }}</div>
            <div class="rail-user-scope">{{ auth.appId }}</div>
          </div>
        </div>
        <button class="link-button" @click="logout">退出</button>
      </div>
    </nav>

    <main class="main">
      <slot />
    </main>
  </div>
</template>
