import { reactive } from 'vue'

const STORAGE_KEY = 'ai-service-auth'

function emptyState() {
  return {
    token: '',
    appId: '',
    appIdHeader: 'X-App-Id',
    user: null,
    expiresAt: null,
  }
}

/** 认证状态只有一个副本，存在 localStorage 里，刷新页面不丢。 */
export const auth = reactive(emptyState())

export function restoreAuth() {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return
  try {
    const saved = JSON.parse(raw)
    Object.assign(auth, emptyState(), saved)
  } catch {
    localStorage.removeItem(STORAGE_KEY)
  }
}

export function saveAuth({ token, appId, appIdHeader, user, expiresAt }) {
  Object.assign(auth, {
    token,
    appId: appId ?? auth.appId,
    appIdHeader: appIdHeader ?? auth.appIdHeader,
    user: user ?? null,
    expiresAt: expiresAt ?? null,
  })
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      token: auth.token,
      appId: auth.appId,
      appIdHeader: auth.appIdHeader,
      user: auth.user,
      expiresAt: auth.expiresAt,
    }),
  )
}

export function clearAuth() {
  Object.assign(auth, emptyState())
  localStorage.removeItem(STORAGE_KEY)
}

/** 本地先判一次过期，省得每个请求都吃一个 401；真正的判定仍在服务端。 */
export function isAuthenticated() {
  if (!auth.token) return false
  if (!auth.expiresAt) return true
  return new Date(auth.expiresAt).getTime() > Date.now()
}
