import { auth, clearAuth } from './store'

//: 与后端一致：每条消息最多 4 张、单张 ≤5MB（服务端会再校验一次）。
export const MAX_IMAGES = 4
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message)
    this.status = status
    this.code = code
  }
}

function authHeaders(extra = {}) {
  const headers = { ...extra }
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`
  if (auth.appId) headers[auth.appIdHeader || 'X-App-Id'] = auth.appId
  return headers
}

/** 两个后端的错误信封长得不一样，这里都收口成 ApiError。 */
async function toError(response) {
  let code = `http_${response.status}`
  let message = response.statusText || '请求失败'
  try {
    const body = await response.json()
    if (body?.error && typeof body.error === 'object') {
      code = body.error.code ?? code
      message = body.error.message ?? message
    } else if (typeof body?.error === 'string') {
      code = body.error
      message = body.message ?? message
    } else if (typeof body?.detail === 'string') {
      message = body.detail
    } else if (Array.isArray(body?.detail)) {
      // FastAPI 的 422：把第一条校验信息翻成人话。
      const first = body.detail[0]
      code = 'invalid_request'
      message = first?.msg ? `${first.msg}（${(first.loc ?? []).slice(1).join('.')}）` : '参数不合法'
    }
  } catch {
    /* body 不是 JSON，就用状态码兜底 */
  }
  if (response.status === 401) clearAuth()
  return new ApiError(response.status, code, message)
}

async function request(path, { method = 'GET', body, headers } = {}) {
  const response = await fetch(path, {
    method,
    headers: authHeaders(body ? { 'Content-Type': 'application/json', ...headers } : headers),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) throw await toError(response)
  if (response.status === 204) return null
  return response.json()
}

/* ------------------------------- 认证服务 ------------------------------- */

export const fetchPublicConfig = () => request('/auth/config')

export const register = (username, password) =>
  request('/auth/register', { method: 'POST', body: { username, password } })

export async function login(username, password) {
  const data = await request('/auth/login', { method: 'POST', body: { username, password } })
  return {
    token: data.access_token,
    appId: data.app_id,
    user: data.user,
    expiresAt: data.expires_at,
  }
}

export const me = () => request('/auth/me')

/* ------------------------------- 业务接口 ------------------------------- */

export const listAgents = () => request('/api/v1/agents')

export const listConversations = (agentId) =>
  request(`/api/v1/conversations?agent_id=${encodeURIComponent(agentId)}&limit=30`)

export const listMessages = (conversationId) =>
  request(`/api/v1/conversations/${conversationId}/messages?limit=100`)

export const deleteConversation = (conversationId) =>
  request(`/api/v1/conversations/${conversationId}`, { method: 'DELETE' })

/* ------------------------------ SSE 流式对话 ----------------------------- */

function parseSseBlock(block) {
  let event = 'message'
  const data = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  try {
    return { event, data: JSON.parse(data.join('\n')) }
  } catch {
    return { event, data: { raw: data.join('\n') } }
  }
}

/**
 * EventSource 只能发 GET，而我们的对话是 POST，所以手动读流。
 * 产出 {event, data}，event 取值为 meta / token / tool_call / tool_result / error / done。
 */
export async function* streamChat({ agentId, message, conversationId, images = [], signal }) {
  const response = await fetch(`/api/v1/agents/${agentId}/chat/stream`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      message,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(images.length ? { images } : {}),
    }),
    signal,
  })
  if (!response.ok) throw await toError(response)
  if (!response.body) throw new ApiError(0, 'no_stream', '浏览器没有返回可读流')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let index = buffer.indexOf('\n\n')
    while (index >= 0) {
      const parsed = parseSseBlock(buffer.slice(0, index))
      buffer = buffer.slice(index + 2)
      if (parsed) yield parsed
      index = buffer.indexOf('\n\n')
    }
  }
}

export function fileToImage(file) {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('image/')) {
      reject(new Error('只能上传图片'))
      return
    }
    if (file.size > MAX_IMAGE_BYTES) {
      reject(new Error(`单张图片不能超过 ${MAX_IMAGE_BYTES / 1024 / 1024}MB`))
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result)
      resolve({
        preview: dataUrl,
        // 服务端既认 data URL 前缀，也认裸 base64；这里给裸的那半 + 显式 mime。
        base64: dataUrl.slice(dataUrl.indexOf(',') + 1),
        mime_type: file.type,
        name: file.name,
      })
    }
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })
}
