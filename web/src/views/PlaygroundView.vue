<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppShell from '../layouts/AppShell.vue'
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

const debugOpen = ref(true)
const scrollBox = ref(null)
const fileInput = ref(null)
const abortController = ref(null)
let messageSeq = 0

const activeAgent = computed(() => agents.value.find((agent) => agent.agent_id === activeAgentId.value))
const canSend = computed(
  () => !sending.value && (draft.value.trim().length > 0 || pendingImages.value.length > 0),
)
const lastRun = computed(() => {
  for (let index = messages.value.length - 1; index >= 0; index -= 1) {
    if (messages.value[index].run) return messages.value[index]
  }
  return null
})

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

function syncQuery() {
  const query = {}
  if (activeAgentId.value) query.agent = activeAgentId.value
  if (activeConversationId.value) query.conversation = activeConversationId.value
  router.replace({ query })
}

async function loadAgents() {
  agents.value = await listAgents()
}

async function loadConversations() {
  if (!activeAgentId.value) return
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
      run: null,
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
      pendingImages.value.push(await fileToImage(file))
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
    run: null,
  })

  const assistant = reactive({
    id: nextId(),
    role: 'assistant',
    content: '',
    images: [],
    tools: [],
    pending: true,
    run: { runId: null, ttftMs: null, totalMs: null, chars: 0, toolCount: 0, events: [] },
  })
  messages.value.push(assistant)

  draft.value = ''
  pendingImages.value = []
  sending.value = true
  abortController.value = new AbortController()
  const startedAt = performance.now()
  await scrollToBottom()

  const record = (event, data) => {
    assistant.run.events.push({ at: Math.round(performance.now() - startedAt), event, data })
  }

  try {
    const stream = streamChat({
      agentId: activeAgentId.value,
      message: text,
      conversationId: activeConversationId.value,
      images: payloadImages,
      signal: abortController.value.signal,
    })

    for await (const { event, data } of stream) {
      record(event, data)

      if (event === 'meta') {
        activeConversationId.value = data.conversation_id
        assistant.run.runId = data.thread_id
        syncQuery()
      } else if (event === 'token') {
        if (assistant.run.ttftMs === null) assistant.run.ttftMs = Math.round(performance.now() - startedAt)
        assistant.content += data.delta ?? ''
        assistant.run.chars = assistant.content.length
        await scrollToBottom()
      } else if (event === 'tool_call') {
        assistant.tools.push({
          callId: data.call_id,
          name: data.name,
          arguments: data.arguments,
          content: '',
          done: false,
        })
        assistant.run.toolCount = assistant.tools.length
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
          assistant.run.toolCount = assistant.tools.length
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
    assistant.run.totalMs = Math.round(performance.now() - startedAt)
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

async function restoreFromQuery() {
  const wanted = typeof route.query.agent === 'string' ? route.query.agent : ''
  activeAgentId.value = agents.value.some((agent) => agent.agent_id === wanted)
    ? wanted
    : (agents.value[0]?.agent_id ?? '')
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
  <AppShell>
    <div class="playground" :class="{ 'with-debug': debugOpen }">
      <aside class="side-panel">
        <div class="side-head">
          <div class="side-title">智能体</div>
          <span class="count">{{ agents.length }}</span>
        </div>
        <div class="side-scroll">
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

          <div class="side-head" style="margin-top: 12px">
            <div class="side-title">会话</div>
            <button class="link-button" @click="startNewConversation">新建</button>
          </div>
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
          <p v-if="!conversations.length" class="empty-hint">还没有会话，发一条消息就会新建。</p>
        </div>
      </aside>

      <section class="chat">
        <header class="chat-head">
          <div>
            <h1>{{ activeAgent?.name || '选择一个智能体' }}</h1>
            <div class="agent-desc">{{ activeAgent?.description }}</div>
          </div>
          <span class="spacer"></span>
          <button class="chip" @click="debugOpen = !debugOpen">
            {{ debugOpen ? '隐藏调试' : '调试面板' }}
          </button>
          <button class="chip" @click="startNewConversation">新对话</button>
        </header>

        <div ref="scrollBox" class="messages">
          <div class="thread">
            <div v-if="!messages.length" class="empty-state">
              <h2>开始一次测试</h2>
              <p>SSE 增量、工具调用、耗时都会记录在右侧调试面板里。</p>
              <div class="suggestions">
                <button v-for="item in suggestions" :key="item" class="chip" @click="draft = item">
                  {{ item }}
                </button>
              </div>
            </div>

            <div v-for="message in messages" :key="message.id" class="msg" :class="message.role">
              <div class="msg-body">
                <div class="msg-role">
                  {{ message.role === 'user' ? '我' : '智能体' }}
                  <span v-if="message.role === 'assistant' && message.run?.totalMs" class="msg-meta">
                    {{ message.run.totalMs }}ms · 首字 {{ message.run.ttftMs ?? '—' }}ms
                  </span>
                </div>
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
              <span>{{ auth.appId }} · {{ auth.user?.username }}</span>
              <span v-if="error" class="error">{{ error }}</span>
            </div>
          </div>
        </div>
      </section>

      <aside v-if="debugOpen" class="debug-panel">
        <div class="side-head">
          <div class="side-title">运行详情</div>
          <span class="count">{{ lastRun?.run?.events.length ?? 0 }} 事件</span>
        </div>
        <div class="debug-scroll">
          <template v-if="lastRun?.run">
            <dl class="kv">
              <dt>会话</dt>
              <dd class="mono" :title="activeConversationId || ''">{{ activeConversationId || '—' }}</dd>
              <dt>thread</dt>
              <dd class="mono" :title="lastRun.run.runId || ''">{{ lastRun.run.runId || '—' }}</dd>
              <dt>首字延迟</dt>
              <dd>{{ lastRun.run.ttftMs ?? '—' }} ms</dd>
              <dt>总耗时</dt>
              <dd>{{ lastRun.run.totalMs ?? '—' }} ms</dd>
              <dt>回答字数</dt>
              <dd>{{ lastRun.run.chars }}</dd>
              <dt>工具调用</dt>
              <dd>{{ lastRun.run.toolCount }}</dd>
              <dt>事件数</dt>
              <dd>{{ lastRun.run.events.length }}</dd>
            </dl>

            <div class="side-title" style="margin: 14px 0 6px">事件流</div>
            <ol class="events">
              <li v-for="(item, index) in lastRun.run.events" :key="index">
                <span class="mono">{{ item.at }}ms</span>
                <span class="event-name">{{ item.event }}</span>
                <span class="event-data">{{ JSON.stringify(item.data).slice(0, 90) }}</span>
              </li>
            </ol>
          </template>
          <p v-else class="empty-hint">发一条消息，这里会显示延迟、工具调用和原始 SSE 事件。</p>
        </div>
      </aside>
    </div>

    <input
      ref="fileInput"
      class="hidden-input"
      type="file"
      accept="image/jpeg,image/png,image/webp,image/gif"
      multiple
      @change="pickImages"
    />
  </AppShell>
</template>
