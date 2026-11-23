<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { register } from '../api'

const router = useRouter()
const form = reactive({ username: '', password: '', confirm: '' })
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  if (form.password !== form.confirm) {
    error.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  try {
    await register(form.username.trim(), form.password)
    router.push({ name: 'login', query: { registered: '1' } })
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <form class="auth-card" @submit.prevent="submit">
      <div class="auth-brand">
        <span class="brand-mark">耘</span>
        <div>
          <div class="sidebar-title">AI 服务控制台</div>
          <div class="sidebar-sub">注册后自动回到登录</div>
        </div>
      </div>

      <h1 class="auth-title">注册</h1>
      <p class="auth-subtitle">用户名 3-32 位，可用中文、字母、数字、下划线、点、@、中划线。</p>

      <div v-if="error" class="alert">{{ error }}</div>

      <label class="field">
        <span class="field-label">用户名</span>
        <input v-model="form.username" autocomplete="username" placeholder="farmer01" required />
      </label>

      <label class="field">
        <span class="field-label">密码</span>
        <input
          v-model="form.password"
          type="password"
          autocomplete="new-password"
          placeholder="至少 8 位"
          required
        />
      </label>

      <label class="field">
        <span class="field-label">确认密码</span>
        <input v-model="form.confirm" type="password" autocomplete="new-password" required />
      </label>

      <button class="btn" type="submit" :disabled="loading">
        {{ loading ? '创建中…' : '创建账号' }}
      </button>

      <p class="auth-footer">已经有账号？<RouterLink to="/login">去登录</RouterLink></p>
    </form>
  </div>
</template>
