<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  MAX_IMAGES,
  fileToImage,
  listAgents,
  listConversations,
  listMessages,
  streamChat,
} from '../api'
import { renderMarkdown } from '../markdown'
import { auth, clearAuth, isAuthenticated } from '../store'

const router = useRouter()
const route = useRoute()

const agents = ref([])
const activeAgentId = ref('')
const conversations = ref([])
const activeConversationId = ref(null)
const messages = ref([])

const draft = ref('')
const pendingImages = ref([])
const sending = ref(false)
const error = ref('')

const scrollBox = ref(null)
const fileInput = ref(null)
const abortController = ref(null)
let messageSeq = 0

const activeAgent = computed(() => agents.value.find((agent) => agent.agent_id === activeAgentId.value))

const canSend = computed(
  () => !sending.value && (draft.value.trim().length > 0 || pendingImages.value.length > 0),
)

const suggestions = [
  '小麦抽穗期叶子发黄，可能缺什么？',
  '玉米叶片有灰白色斑点，怎么判断？',
  '河南 2026 年小麦赤霉病有什么预警？',
]

function nextId() {
  messageSeq += 1
  return `local-${messageSeq}`
}

async function scrollToBottom() {
  await nextTick()
  const box = scrollBox.value
  if (box) box.scrollTop = box.scrollHeight
}

function handleUnauthorized(err) {
  if (err?.status === 401) {
    clearAuth()
    router.push('/login')
    return true
  }
  return false
}

async function loadAgents() {
  agents.value = await listAgents()
}

/** Agent / 会话都写进 URL，刷新和分享链接都能回到原处。 */
function syncQuery() {
  const query = {}
  if (activeAgentId.value) query.agent = activeAgentId.value
  if (activeConversationId.value) query.conversation = activeConversationId.value
  router.replace({ query })
}

async function loadConversations() {
  if (!activeAgentId.value) return
  // 接口返回 {items, next_before}，别把信封当数组用。
  const page = await listConversations(activeAgentId.value)
  conversations.value = page.items ?? []
}

async function selectAgent(agentId) {
  if (agentId === activeAgentId.value) return
  activeAgentId.value = agentId
  activeConversationId.value = null
  messages.value = []
  syncQuery()
  await loadConversations()
}

async function openConversation(conversation) {
  if (conversation.id === activeConversationId.value) return
  activeConversationId.value = conversation.id
  syncQuery()
  messages.value = []
  try {
    const history = await listMessages(conversation.id)
    messages.value = history.items.map((item) => ({
      id: item.id,
      role: item.role,
      content: item.content,
      images: [],
      tools: [],
      pending: false,
    }))
    await scrollToBottom()
  } catch (err) {
    if (!handleUnauthorized(err)) error.value = err.message
  }
}

function startNewConversation() {
  activeConversationId.value = null
  messages.value = []
  error.value = ''
  syncQuery()
}

async function pickImages(event) {
  const files = Array.from(event.target.files ?? [])
  event.target.value = ''
  for (const file of files) {
    if (pendingImages.value.length >= MAX_IMAGES) {
      error.value = `每条消息最多 ${MAX_IMAGES} 张图片`
      break
    }
    try {
      const image = await fileToImage(file)
      pendingImages.value.push(image)
    } catch (err) {
      error.value = err.message
    }
  }
}

function removeImage(index) {
  pendingImages.value.splice(index, 1)
}

function stopStreaming() {
  abortController.value?.abort()
}

async function send() {
  if (!canSend.value) return
  error.value = ''

  const text = draft.value.trim()
  const images = pendingImages.value.slice()
  const payloadImages = images.map((image) => ({ base64: image.base64, mime_type: image.mime_type }))

  messages.value.push({
    id: nextId(),
    role: 'user',
    content: text || '（图片）',
    images: images.map((image) => image.preview),
    tools: [],
    pending: false,
  })
  const assistant = reactive({
    id: nextId(),
    role: 'assistant',
    content: '',
    images: [],
    tools: [],
    pending: true,
  })
  messages.value.push(assistant)

  draft.value = ''
  pendingImages.value = []
  sending.value = true
  abortController.value = new AbortController()
  await scrollToBottom()

  try {
    const stream = streamChat({
      agentId: activeAgentId.value,
      message: text,
      conversationId: activeConversationId.value,
      images: payloadImages,
      signal: abortController.value.signal,
    })

    for await (const { event, data } of stream) {
      if (event === 'meta') {
        activeConversationId.value = data.conversation_id
        syncQuery()
      } else if (event === 'token') {
        assistant.content += data.delta ?? ''
        await scrollToBottom()
      } else if (event === 'tool_call') {
        assistant.tools.push({
          callId: data.call_id,
          name: data.name,
          arguments: data.arguments,
          content: '',
          done: false,
        })
        await scrollToBottom()
      } else if (event === 'tool_result') {
        const tool = assistant.tools.find((item) => item.callId === data.call_id)
        if (tool) {
          tool.content = data.content
          tool.done = true
        } else {
          assistant.tools.push({
            callId: data.call_id,
            name: data.name,
            arguments: '',
            content: data.content,
            done: true,
          })
        }
        await scrollToBottom()
      } else if (event === 'error') {
        assistant.content += `\n\n[出错] ${data.message ?? '运行失败'}（${data.code ?? 'error'}）`
      } else if (event === 'done') {
        assistant.pending = false
      }
    }
  } catch (err) {
    if (err?.name === 'AbortError') {
      assistant.content += assistant.content ? '\n\n（已停止）' : '（已停止）'
    } else if (handleUnauthorized(err)) {
      return
    } else {
      error.value = err.message
      assistant.content += assistant.content ? `\n\n[出错] ${err.message}` : `[出错] ${err.message}`
    }
  } finally {
    assistant.pending = false
    sending.value = false
    abortController.value = null
    await loadConversations().catch(() => {})
    await scrollToBottom()
  }
}

function onComposerKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}

function logout() {
  clearAuth()
  router.push('/login')
}

async function restoreFromQuery() {
  const wanted = typeof route.query.agent === 'string' ? route.query.agent : ''
  const known = agents.value.some((agent) => agent.agent_id === wanted)
  activeAgentId.value = known ? wanted : (agents.value[0]?.agent_id ?? '')
  await loadConversations()

  const conversationId = typeof route.query.conversation === 'string' ? route.query.conversation : ''
  const target = conversations.value.find((item) => item.id === conversationId)
  if (target) await openConversation(target)
}

onMounted(async () => {
  if (!isAuthenticated()) {
    router.push('/login')
    return
  }
  try {
    await loadAgents()
    await restoreFromQuery()
  } catch (err) {
    if (!handleUnauthorized(err)) error.value = err.message
  }
})
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="sidebar-head">
        <div class="sidebar-title"><span class="brand-mark">耘</span> AI 服务控制台</div>
        <div class="sidebar-sub">{{ auth.appId }} · 已认证</div>
      </div>

      <div class="sidebar-body">
        <div class="sidebar-section-label">智能体</div>
        <button
          v-for="agent in agents"
          :key="agent.agent_id"
          class="nav-item"
          :class="{ active: agent.agent_id === activeAgentId }"
          @click="selectAgent(agent.agent_id)"
        >
          <div class="nav-item-title">{{ agent.name }}</div>
          <div class="nav-item-meta">{{ agent.agent_id }}</div>
        </button>

        <div class="sidebar-section-label" style="margin-top: 14px">会话</div>
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          class="nav-item conv-item"
          :class="{ active: conversation.id === activeConversationId }"
          @click="openConversation(conversation)"
        >
          <div class="nav-item-title">{{ conversation.title || '未命名会话' }}</div>
          <div class="nav-item-meta">{{ new Date(conversation.updated_at).toLocaleString() }}</div>
        </button>
        <p v-if="!conversations.length" class="nav-item-meta" style="padding: 4px 10px">
          还没有会话，直接发消息即可新建。
        </p>
      </div>

      <div class="sidebar-foot">
        <span class="avatar">{{ (auth.user?.username || '?').slice(0, 1).toUpperCase() }}</span>
        <div class="who">
          <div class="name">{{ auth.user?.username || '未知用户' }}</div>
          <div class="hint">user_id {{ (auth.user?.user_id || '').slice(0, 8) }}</div>
        </div>
        <button class="link-button" @click="logout">退出</button>
      </div>
    </aside>

    <main class="chat">
      <header class="chat-head">
        <div>
          <h1>{{ activeAgent?.name || '选择一个智能体' }}</h1>
          <div class="agent-desc">{{ activeAgent?.description }}</div>
        </div>
        <span class="spacer"></span>
        <button class="chip" @click="startNewConversation">新对话</button>
      </header>

      <div ref="scrollBox" class="messages">
        <div class="thread">
          <div v-if="!messages.length" class="empty-state">
            <h2>问点什么？</h2>
            <p>消息会通过 SSE 流式返回，工具调用过程也会实时显示。</p>
            <div class="suggestions">
              <button v-for="text in suggestions" :key="text" class="chip" @click="draft = text">
                {{ text }}
              </button>
            </div>
          </div>

          <div v-for="message in messages" :key="message.id" class="msg" :class="message.role">
            <div class="msg-body">
              <div class="msg-role">{{ message.role === 'user' ? '我' : '智能体' }}</div>
              <div class="bubble" :class="{ pending: message.pending }">
                <div v-if="message.images?.length" class="thumbs">
                  <img v-for="(src, index) in message.images" :key="index" :src="src" alt="上传的图片" />
                </div>
                <div
                  v-if="message.content && message.role === 'assistant'"
                  class="md"
                  v-html="renderMarkdown(message.content)"
                ></div>
                <template v-else-if="message.content">{{ message.content }}</template>
                <span v-else-if="message.pending" class="nav-item-meta">思考中…</span>

                <details v-for="tool in message.tools" :key="tool.callId" class="tool">
                  <summary>
                    <span class="tool-name">{{ tool.name }}</span>
                    <span>{{ tool.done ? '已返回' : '调用中…' }}</span>
                  </summary>
                  <pre v-if="tool.arguments">参数：{{ tool.arguments }}</pre>
                  <pre v-if="tool.content">结果：{{ tool.content }}</pre>
                </details>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="composer-wrap">
        <div class="composer">
          <div v-if="pendingImages.length" class="attachments">
            <div v-for="(image, index) in pendingImages" :key="index" class="attachment">
              <img :src="image.preview" :alt="image.name" />
              <button type="button" @click="removeImage(index)">×</button>
            </div>
          </div>

          <div class="input-row">
            <textarea
              v-model="draft"
              rows="1"
              placeholder="说说你的问题，Enter 发送，Shift+Enter 换行"
              @keydown="onComposerKeydown"
            ></textarea>
            <button
              class="icon-button"
              type="button"
              title="添加图片（最多 4 张，单张 ≤5MB）"
              :disabled="sending || pendingImages.length >= MAX_IMAGES"
              @click="fileInput?.click()"
            >
              ＋
            </button>
            <button v-if="!sending" class="send-button" type="button" :disabled="!canSend" @click="send">
              发送
            </button>
            <button v-else class="send-button" type="button" @click="stopStreaming">停止</button>
          </div>

          <div class="composer-hint">
            <span>支持图片（base64 上传）· 工具调用会展示过程</span>
            <span v-if="error" class="error">{{ error }}</span>
          </div>
        </div>
      </div>
    </main>

    <input
      ref="fileInput"
      class="hidden-input"
      type="file"
      accept="image/jpeg,image/png,image/webp,image/gif"
      multiple
      @change="pickImages"
    />
  </div>
</template>
