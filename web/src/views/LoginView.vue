<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchPublicConfig, login } from '../api'
import { saveAuth } from '../store'

const router = useRouter()
const route = useRoute()

const form = reactive({ username: '', password: '' })
const justRegistered = computed(() => route.query.registered === '1')
const loading = ref(false)
const error = ref('')
const appId = ref('')
const appIdHeader = ref('X-App-Id')
const ttl = ref(0)

onMounted(async () => {
  // 拿 app_id 与 token 有效期，失败不影响登录，只是少了提示信息。
  try {
    const config = await fetchPublicConfig()
    appId.value = config.app_id
    appIdHeader.value = config.app_id_header
    ttl.value = config.token_ttl_minutes
  } catch {
    /* 认证服务没起，登录时会给出明确报错 */
  }
})

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const result = await login(form.username.trim(), form.password)
    saveAuth({
      token: result.token,
      appId: result.appId || appId.value,
      appIdHeader: appIdHeader.value,
      user: result.user,
      expiresAt: result.expiresAt,
    })
    router.push(typeof route.query.redirect === 'string' ? route.query.redirect : '/playground')
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
          <div class="sidebar-sub">登录后即可调用智能体</div>
        </div>
      </div>

      <h1 class="auth-title">登录</h1>
      <p class="auth-subtitle">
        账号由认证服务签发 JWT，主服务按 <code>{{ appIdHeader }}: {{ appId || '…' }}</code> 验签。
      </p>

      <div v-if="justRegistered" class="alert alert-success">账号已创建，用刚才的密码登录即可。</div>
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
          autocomplete="current-password"
          placeholder="至少 8 位"
          required
        />
      </label>

      <button class="btn" type="submit" :disabled="loading">
        {{ loading ? '登录中…' : '登录' }}
      </button>

      <p class="auth-footer">
        还没有账号？<RouterLink to="/register">注册一个</RouterLink>
        <span v-if="ttl"> · token 有效期 {{ ttl }} 分钟</span>
      </p>
    </form>
  </div>
</template>
