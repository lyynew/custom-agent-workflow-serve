<script setup>
import { computed, ref } from 'vue'

import { decodeJwt, probeToken, smokeRun } from '../api'
import AppShell from '../layouts/AppShell.vue'
import { auth, clearAuth, isAuthenticated } from '../store'
import { useRouter } from 'vue-router'

const router = useRouter()

const appId = ref(auth.appId || 'agriculture_app')
const token = ref('')
const loading = ref(false)
const smoke = ref(null)
const error = ref('')
const result = ref(null)

const decoded = ref(null)

const expiresIn = computed(() => {
  const exp = decoded.value?.payload?.exp
  if (!exp) return null
  const seconds = Math.round(exp - Date.now() / 1000)
  return seconds
})

const curlSnippet = computed(
  () =>
    `curl -s http://127.0.0.1:2026/api/v1/whoami \\\n  -H "Authorization: Bearer $TOKEN" \\\n  -H "X-App-Id: ${appId.value}"`,
)

async function verify() {
  error.value = ''
  result.value = null
  smoke.value = null

  if (!token.value.trim()) {
    error.value = '先粘贴一个 token'
    return
  }

  try {
    decoded.value = decodeJwt(token.value.trim())
  } catch (err) {
    decoded.value = null
    error.value = `Token 解析失败：${err.message}`
    return
  }

  loading.value = true
  try {
    result.value = await probeToken({ token: token.value.trim(), appId: appId.value })
  } catch (err) {
    if (err.status === 401) {
      error.value = `验签失败：${err.message}（检查 X-App-Id 与密钥配置是否匹配）`
    } else {
      error.value = err.message
    }
  } finally {
    loading.value = false
  }
}

async function runSmoke() {
  if (!result.value) return
  smoke.value = { state: 'running' }
  try {
    const response = await smokeRun({ token: token.value.trim(), appId: appId.value })
    smoke.value = {
      state: 'ok',
      text: response.assistant_message?.content ?? '(无内容)',
      runId: response.run_id,
    }
  } catch (err) {
    smoke.value = { state: 'error', text: err.message }
  }
}

function formatList(items) {
  if (!items?.length) return '（无）'
  return items.includes('*') ? '全部（*）' : items.join('、')
}

function logout() {
  clearAuth()
  router.push('/login')
}

if (!isAuthenticated()) router.push('/login')
</script>

<template>
  <AppShell>
    <div class="page">
      <header class="page-head">
        <div>
          <h1>Token 接入</h1>
          <p>把别处签发的 token 粘进来，验证它能不能调通本服务——不改变当前登录态。</p>
        </div>
        <button class="chip" @click="logout">退出</button>
      </header>

      <div class="split">
        <section class="card">
          <div class="card-title">请求凭据</div>

          <label class="field">
            <span class="field-label">X-App-Id</span>
            <input v-model="appId" placeholder="agriculture_app" />
          </label>

          <label class="field">
            <span class="field-label">JWT</span>
            <textarea
              v-model="token"
              rows="7"
              class="mono-input"
              placeholder="粘贴对方系统签发的 access_token"
            ></textarea>
          </label>

          <div class="actions">
            <button class="btn" :disabled="loading" @click="verify">
              {{ loading ? '验证中…' : '验证 token' }}
            </button>
            <button class="btn btn-ghost" :disabled="!result" @click="runSmoke">试跑 echo_agent</button>
          </div>

          <div v-if="error" class="alert" style="margin-top: 14px">{{ error }}</div>

          <div class="hint-block">
            <div class="card-title" style="margin-bottom: 6px">等价 curl</div>
            <pre class="code-block">{{ curlSnippet }}</pre>
          </div>
        </section>

        <section class="card">
          <div class="card-title">验证结果</div>

          <p v-if="!result && !error" class="empty-hint">
            粘贴 token 后点「验证 token」。客户端只解析不验签，验签发生在服务端。
          </p>

          <template v-if="result">
            <dl class="kv">
              <dt>身份</dt>
              <dd class="mono">{{ result.whoami.identity }}</dd>
              <dt>应用</dt>
              <dd>{{ result.app.app_name }}（{{ result.app.app_id }}）</dd>
              <dt>可用 Agent</dt>
              <dd>{{ formatList(result.whoami.allowed_agents) }}</dd>
              <dt>可用 Workflow</dt>
              <dd>{{ formatList(result.whoami.allowed_workflows) }}</dd>
              <dt>实际可列出</dt>
              <dd>{{ result.agents.length }} 个：{{ result.agents.map((a) => a.agent_id).join('、') }}</dd>
            </dl>

            <div v-if="smoke" class="smoke" :class="smoke.state">
              <span class="smoke-label">
                {{ smoke.state === 'running' ? '试跑中…' : smoke.state === 'ok' ? '试跑成功' : '试跑失败' }}
              </span>
              <span v-if="smoke.text" class="mono">{{ smoke.text }}</span>
              <span v-if="smoke.runId" class="nav-item-meta">run_id: {{ smoke.runId }}</span>
            </div>
          </template>

          <template v-if="decoded">
            <div class="card-title" style="margin: 18px 0 6px">Token 内容（未验签）</div>
            <dl class="kv">
              <dt>算法</dt>
              <dd class="mono">{{ decoded.header.alg }}</dd>
              <dt>user_id</dt>
              <dd class="mono">{{ decoded.payload.user_id ?? decoded.payload.sub ?? '（缺失）' }}</dd>
              <dt>app_id</dt>
              <dd class="mono">{{ decoded.payload.app_id ?? '（未声明）' }}</dd>
              <dt>有效期</dt>
              <dd>
                <span v-if="expiresIn === null" class="muted">无 exp（服务端会拒）</span>
                <span v-else-if="expiresIn <= 0" class="danger">已过期 {{ -expiresIn }} 秒</span>
                <span v-else>还有 {{ Math.floor(expiresIn / 60) }} 分钟</span>
              </dd>
            </dl>
            <pre class="code-block">{{ JSON.stringify(decoded.payload, null, 2) }}</pre>
          </template>
        </section>
      </div>
    </div>
  </AppShell>
</template>
