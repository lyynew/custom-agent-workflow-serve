<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { auditSummary, listAuditLogs } from '../api'
import AppShell from '../layouts/AppShell.vue'
import { auth, clearAuth, isAuthenticated } from '../store'

const router = useRouter()

const summary = ref(null)
const items = ref([])
const loading = ref(false)
const error = ref('')
const nextBefore = ref(null)
const selected = ref(null)

const filters = ref({ status: '', method: '', path: '' })
const windowHours = ref(24)

const windows = [
  { label: '1 小时', value: 1 },
  { label: '24 小时', value: 24 },
  { label: '7 天', value: 24 * 7 },
]

const hasMore = computed(() => Boolean(nextBefore.value))

function statusClass(code) {
  if (code >= 500) return 'badge danger'
  if (code >= 400) return 'badge warn'
  return 'badge ok'
}

function handleUnauthorized(err) {
  if (err?.status === 401) {
    clearAuth()
    router.push('/login')
    return true
  }
  return false
}

async function loadSummary() {
  summary.value = await auditSummary(windowHours.value)
}

async function loadLogs({ append = false } = {}) {
  loading.value = true
  error.value = ''
  try {
    const page = await listAuditLogs({
      status: filters.value.status || undefined,
      method: filters.value.method || undefined,
      path: filters.value.path || undefined,
      limit: 50,
      before: append ? nextBefore.value : undefined,
    })
    items.value = append ? [...items.value, ...page.items] : page.items
    nextBefore.value = page.next_before
  } catch (err) {
    if (!handleUnauthorized(err)) error.value = err.message
  } finally {
    loading.value = false
  }
}

async function refresh() {
  await Promise.all([loadSummary(), loadLogs()])
}

async function changeWindow(hours) {
  windowHours.value = hours
  await loadSummary().catch((err) => {
    if (!handleUnauthorized(err)) error.value = err.message
  })
}

function resetFilters() {
  filters.value = { status: '', method: '', path: '' }
  refresh()
}

function formatTime(value) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function logout() {
  clearAuth()
  router.push('/login')
}

onMounted(async () => {
  if (!isAuthenticated()) {
    router.push('/login')
    return
  }
  await refresh()
})
</script>

<template>
  <AppShell>
    <div class="page">
      <header class="page-head">
        <div>
          <h1>审计日志</h1>
          <p>本应用（{{ auth.appId }}）的业务请求流水，只记录元信息，不记录请求体。</p>
        </div>
        <div class="actions">
          <button class="chip" :disabled="loading" @click="refresh">刷新</button>
          <button class="chip" @click="logout">退出</button>
        </div>
      </header>

      <section class="stats">
        <div class="stat">
          <div class="stat-label">请求数</div>
          <div class="stat-value">{{ summary?.requests ?? '—' }}</div>
          <div class="stat-hint">最近 {{ windowHours }} 小时</div>
        </div>
        <div class="stat">
          <div class="stat-label">错误率</div>
          <div class="stat-value" :class="{ danger: (summary?.error_rate ?? 0) > 0.05 }">
            {{ summary ? (summary.error_rate * 100).toFixed(1) + '%' : '—' }}
          </div>
          <div class="stat-hint">
            5xx {{ summary?.server_errors ?? 0 }} · 4xx {{ summary?.client_errors ?? 0 }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">P95 延时</div>
          <div class="stat-value">{{ summary?.p95_ms ?? '—' }}<span class="unit">ms</span></div>
          <div class="stat-hint">平均 {{ summary?.avg_ms ?? 0 }} ms</div>
        </div>
        <div class="stat">
          <div class="stat-label">活跃用户</div>
          <div class="stat-value">{{ summary?.users ?? '—' }}</div>
          <div class="stat-hint">去重 user_id</div>
        </div>
      </section>

      <section class="card filter-bar">
        <div class="window-group">
          <button
            v-for="item in windows"
            :key="item.value"
            class="chip"
            :class="{ active: windowHours === item.value }"
            @click="changeWindow(item.value)"
          >
            {{ item.label }}
          </button>
        </div>

        <select v-model="filters.status" class="select" @change="loadLogs()">
          <option value="">全部状态</option>
          <option value="2xx">2xx 成功</option>
          <option value="4xx">4xx 客户端错误</option>
          <option value="5xx">5xx 服务端错误</option>
        </select>

        <select v-model="filters.method" class="select" @change="loadLogs()">
          <option value="">全部方法</option>
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="DELETE">DELETE</option>
        </select>

        <input
          v-model="filters.path"
          class="select"
          style="min-width: 200px"
          placeholder="路径前缀，如 /api/v1/agents"
          @keydown.enter="loadLogs()"
        />

        <button class="chip" @click="loadLogs()">筛选</button>
        <button class="link-button" @click="resetFilters">重置</button>
      </section>

      <div v-if="error" class="alert">{{ error }}</div>

      <section class="card table-card">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 168px">时间</th>
              <th style="width: 74px">方法</th>
              <th>路径</th>
              <th style="width: 76px">状态</th>
              <th style="width: 88px">耗时</th>
              <th style="width: 130px">用户</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.id" class="row" @click="selected = row">
              <td class="mono">{{ formatTime(row.created_at) }}</td>
              <td class="mono">{{ row.method }}</td>
              <td class="mono path">{{ row.path }}</td>
              <td><span :class="statusClass(row.status_code)">{{ row.status_code }}</span></td>
              <td class="mono">{{ row.duration_ms }} ms</td>
              <td class="mono ellipsis">{{ row.user_id }}</td>
            </tr>
            <tr v-if="!items.length && !loading">
              <td colspan="6" class="empty-hint" style="padding: 24px; text-align: center">
                还没有记录。去测试台发一条消息，回来刷新看看。
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="hasMore" class="load-more">
          <button class="chip" :disabled="loading" @click="loadLogs({ append: true })">
            {{ loading ? '加载中…' : '加载更多' }}
          </button>
        </div>
      </section>
    </div>

    <div v-if="selected" class="drawer-backdrop" @click.self="selected = null">
      <aside class="drawer">
        <header class="drawer-head">
          <div>
            <div class="side-title">请求详情</div>
            <div class="nav-item-meta mono">{{ selected.id }}</div>
          </div>
          <button class="link-button" @click="selected = null">关闭</button>
        </header>
        <dl class="kv">
          <dt>应用</dt>
          <dd class="mono">{{ selected.app_id }}</dd>
          <dt>用户</dt>
          <dd class="mono">{{ selected.user_id }}</dd>
          <dt>请求</dt>
          <dd class="mono">{{ selected.method }} {{ selected.path }}</dd>
          <dt>状态</dt>
          <dd><span :class="statusClass(selected.status_code)">{{ selected.status_code }}</span></dd>
          <dt>耗时</dt>
          <dd>{{ selected.duration_ms }} ms</dd>
          <dt>request_id</dt>
          <dd class="mono">{{ selected.request_id || '—' }}</dd>
          <dt>来源 IP</dt>
          <dd class="mono">{{ selected.client_ip || '—' }}</dd>
          <dt>User-Agent</dt>
          <dd class="mono wrap">{{ selected.user_agent || '—' }}</dd>
          <dt>时间</dt>
          <dd>{{ formatTime(selected.created_at) }}</dd>
        </dl>
        <p class="empty-hint" style="margin-top: 14px">
          审计只记元信息：请求体、Authorization、token 都不会落库。
        </p>
      </aside>
    </div>
  </AppShell>
</template>
