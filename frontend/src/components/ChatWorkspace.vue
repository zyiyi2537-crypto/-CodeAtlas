<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  Bot,
  Brain,
  Clock3,
  FileCode,
  History,
  MessageSquarePlus,
  PanelLeft,
  PanelRight,
  Plus,
  SendHorizontal,
  Sparkles,
  Trash2,
  UserRound,
  X,
} from 'lucide-vue-next'
import { computed, nextTick, reactive, ref, watch } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import CodePreview from '@/components/CodePreview.vue'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate } from '@/format'
import type {
  ChatCitation,
  ChatMessage,
  ChatResponse,
  ChatSession,
  ChatSessionSummary,
  ChatStatus,
  Repository,
  SearchResult,
  UserMemory,
} from '@/types'

const queryClient = useQueryClient()
const activeSessionId = ref('')
const messages = ref<ChatMessage[]>([])
const draft = ref('')
const repositoryId = ref('')
const messageList = ref<HTMLElement | null>(null)
const previewResult = ref<SearchResult | null>(null)
const loadingSession = ref(false)
const sending = ref(false)
const chatError = ref('')
const memoryError = ref('')
const showHistory = ref(false)
const showMemory = ref(false)
const memoryForm = reactive({ kind: 'preference', content: '' })
let retryRequest: { sessionId: string; question: string; requestId: string } | null = null
let sessionRequest = 0

const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})
const chatStatus = useQuery({
  queryKey: ['chat-status'],
  queryFn: async () => (await api.get<ChatStatus>('/chat/status')).data,
})
const sessions = useQuery({
  queryKey: ['chat-sessions'],
  queryFn: async () => (await api.get<ChatSessionSummary[]>('/chat/sessions')).data,
})
const memories = useQuery({
  queryKey: ['user-memories'],
  queryFn: async () => (await api.get<UserMemory[]>('/memories')).data,
})

const selectedSession = computed(() =>
  (sessions.data.value ?? []).find((item) => item.id === activeSessionId.value),
)
const repositoryMap = computed(
  () => new Map((repositories.data.value ?? []).map((repo) => [repo.id, repo])),
)
const suggestionPrompts = [
  '这个项目的入口在哪里？整体结构是怎样的？',
  '权限校验是怎么实现的？',
  '数据库连接在哪里初始化？',
  '有没有处理错误的统一逻辑？',
]
const memoryKinds = [
  { value: 'preference', label: '偏好' },
  { value: 'project', label: '项目' },
  { value: 'environment', label: '环境' },
  { value: 'constraint', label: '约束' },
  { value: 'fact', label: '事实' },
]

watch(
  () => sessions.data.value,
  (items) => {
    if (!activeSessionId.value && items?.[0]) void selectSession(items[0].id)
  },
  { immediate: true },
)

async function scrollToBottom() {
  await nextTick()
  const element = messageList.value
  if (!element) return
  if (typeof element.scrollTo === 'function') {
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
  } else {
    element.scrollTop = element.scrollHeight
  }
}

async function selectSession(id: string) {
  if (sending.value) return
  retryRequest = null
  const request = ++sessionRequest
  activeSessionId.value = id
  loadingSession.value = true
  chatError.value = ''
  showHistory.value = false
  try {
    const { data } = await api.get<ChatSession>(`/chat/sessions/${id}`)
    if (request !== sessionRequest) return
    messages.value = data.messages
    repositoryId.value = data.repository_ids[0] ?? ''
    await scrollToBottom()
  } catch (error) {
    if (request === sessionRequest) chatError.value = errorMessage(error)
  } finally {
    if (request === sessionRequest) loadingSession.value = false
  }
}

function startConversation() {
  if (sending.value) return
  retryRequest = null
  sessionRequest += 1
  activeSessionId.value = ''
  messages.value = []
  draft.value = ''
  chatError.value = ''
  showHistory.value = false
}

async function ensureSession(question: string, requestId: string) {
  if (activeSessionId.value) return activeSessionId.value
  const title = question.replace(/\s+/g, ' ').slice(0, 40) || '新对话'
  const { data } = await api.post<ChatSessionSummary>(
    '/chat/sessions',
    {
      title,
      repository_ids: repositoryId.value ? [repositoryId.value] : [],
      request_id: requestId,
    },
    { headers: csrfHeaders() },
  )
  activeSessionId.value = data.id
  await queryClient.invalidateQueries({ queryKey: ['chat-sessions'] })
  return data.id
}

function createRequestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `request-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

async function ask(question?: string) {
  const text = (question ?? draft.value).trim()
  if (!text || sending.value || chatStatus.data.value?.enabled === false) return
  sending.value = true
  chatError.value = ''
  draft.value = ''
  const optimisticMessageId = `local-user-${Date.now()}`
  const requestScopeId = activeSessionId.value
  const requestId = retryRequest?.sessionId === requestScopeId && retryRequest.question === text
    ? retryRequest.requestId
    : createRequestId()
  let sessionId = ''
  try {
    sessionId = await ensureSession(text, requestId)
    messages.value.push({
      id: optimisticMessageId,
      role: 'user',
      content: text,
      citations: [],
      created_at: new Date().toISOString(),
    })
    await scrollToBottom()
    const { data } = await api.post<ChatResponse>(
      `/chat/sessions/${sessionId}/messages`,
      { question: text, request_id: requestId },
      { headers: csrfHeaders() },
    )
    retryRequest = null
    if (activeSessionId.value !== sessionId) {
      await queryClient.invalidateQueries({ queryKey: ['chat-sessions'] })
      return
    }
    messages.value.push({
      id: `local-assistant-${Date.now()}`,
      role: 'assistant',
      content: data.answer,
      citations: data.citations,
      created_at: new Date().toISOString(),
    })
    await queryClient.invalidateQueries({ queryKey: ['chat-sessions'] })
    await scrollToBottom()
  } catch (error) {
    messages.value = messages.value.filter((message) => message.id !== optimisticMessageId)
    draft.value = text
    retryRequest = {
      sessionId: sessionId || requestScopeId,
      question: text,
      requestId,
    }
    chatError.value = errorMessage(error)
  } finally {
    sending.value = false
  }
}

async function deleteSession(item: ChatSessionSummary) {
  if (sending.value) return
  if (!window.confirm(`删除对话“${item.title}”？此操作会同时删除全部消息。`)) return
  try {
    await api.delete(`/chat/sessions/${item.id}`, { headers: csrfHeaders() })
    if (activeSessionId.value === item.id) startConversation()
    await queryClient.invalidateQueries({ queryKey: ['chat-sessions'] })
  } catch (error) {
    chatError.value = errorMessage(error)
  }
}

async function createMemory() {
  const content = memoryForm.content.trim()
  if (!content) return
  memoryError.value = ''
  try {
    await api.post(
      '/memories',
      { kind: memoryForm.kind, content },
      { headers: csrfHeaders() },
    )
    memoryForm.content = ''
    await queryClient.invalidateQueries({ queryKey: ['user-memories'] })
  } catch (error) {
    memoryError.value = errorMessage(error)
  }
}

async function deleteMemory(memory: UserMemory) {
  if (!window.confirm('删除这条长期记忆？')) return
  memoryError.value = ''
  try {
    await api.delete(`/memories/${memory.id}`, { headers: csrfHeaders() })
    await queryClient.invalidateQueries({ queryKey: ['user-memories'] })
  } catch (error) {
    memoryError.value = errorMessage(error)
  }
}

function openCitation(citation: ChatCitation) {
  if (citation.source_type !== 'code') {
    if (citation.source_url) window.open(citation.source_url, '_blank', 'noopener,noreferrer')
    return
  }
  previewResult.value = {
    repo: citation.repo,
    generation_id: '',
    commit: '',
    path: citation.path,
    language: '',
    symbol: citation.symbol,
    start_line: citation.start_line,
    end_line: citation.end_line,
    score: 0,
    vector_score: 0,
    lexical_score: 0,
    retrieval: 'hybrid',
    snippet: '',
  }
}
</script>

<template>
  <div class="chat-workspace">
    <aside class="chat-account-panel chat-history-panel" :class="{ open: showHistory }">
      <header class="chat-panel-header">
        <div><History :size="17" /><strong>历史对话</strong></div>
        <button class="icon-button mobile-panel-close" type="button" aria-label="关闭历史对话" @click="showHistory = false"><X :size="17" /></button>
      </header>
      <button class="chat-new-session" type="button" :disabled="sending" @click="startConversation">
        <MessageSquarePlus :size="16" />新建对话
      </button>
      <div class="chat-session-list">
        <p v-if="sessions.isPending.value" class="chat-panel-empty">正在加载…</p>
        <p v-else-if="!sessions.data.value?.length" class="chat-panel-empty">还没有历史对话</p>
        <article
          v-for="item in sessions.data.value"
          :key="item.id"
          class="chat-session-item"
          :class="{ active: item.id === activeSessionId }"
        >
          <button class="chat-session-select" type="button" :disabled="sending" @click="selectSession(item.id)">
            <strong>{{ item.title }}</strong>
            <span><Clock3 :size="12" />{{ formatDate(item.updated_at) }}</span>
          </button>
          <button class="icon-button danger chat-session-delete" type="button" :aria-label="`删除对话 ${item.title}`" :disabled="sending" @click="deleteSession(item)"><Trash2 :size="14" /></button>
        </article>
      </div>
    </aside>

    <main class="chat-conversation-panel">
      <header class="chat-conversation-header">
        <div class="chat-mobile-panel-buttons">
          <button class="icon-button" type="button" aria-label="打开历史对话" @click="showHistory = true"><PanelLeft :size="18" /></button>
          <button class="icon-button" type="button" aria-label="打开长期记忆" @click="showMemory = true"><PanelRight :size="18" /></button>
        </div>
        <div class="chat-conversation-title">
          <strong>{{ selectedSession?.title ?? '新对话' }}</strong>
          <span>对话与记忆仅属于当前账号</span>
        </div>
        <label class="chat-scope">
          <span>范围</span>
          <select v-model="repositoryId" aria-label="选择仓库范围" :disabled="Boolean(activeSessionId)">
            <option value="">全部仓库</option>
            <option v-for="repo in repositories.data.value" :key="repo.id" :value="repo.id">{{ repo.name }}</option>
          </select>
        </label>
      </header>

      <div v-if="chatStatus.data.value && !chatStatus.data.value.enabled" class="error-banner chat-status-banner">
        问答服务未配置。管理员可在“模型配置”中添加并启用LLM服务；历史对话和长期记忆仍会保留。
      </div>

      <section ref="messageList" class="chat-thread">
        <div v-if="loadingSession" class="loading-block compact-loading"><div class="loading-spinner" /><span>正在加载历史对话…</span></div>
        <EmptyState
          v-else-if="!messages.length && !sending"
          title="向代码库提问"
          description="回答会附带代码、文档或Wiki引用，并自动保存在当前账号下。"
        >
          <div class="suggestion-grid">
            <button v-for="prompt in suggestionPrompts" :key="prompt" class="suggestion-chip" type="button" @click="ask(prompt)">
              <Sparkles :size="15" />{{ prompt }}
            </button>
          </div>
        </EmptyState>

        <article v-for="message in messages" :key="message.id" class="chat-message" :class="message.role">
          <span class="chat-avatar" aria-hidden="true"><UserRound v-if="message.role === 'user'" :size="17" /><Bot v-else :size="17" /></span>
          <div class="chat-bubble">
            <p class="chat-text">{{ message.content }}</p>
            <div v-if="message.citations?.length" class="citation-list">
              <span class="citation-label">引用</span>
              <button
                v-for="(citation, index) in message.citations"
                :key="`${message.id}-${index}`"
                class="citation-chip"
                type="button"
                :disabled="citation.source_type !== 'code' && !citation.source_url"
                @click="openCitation(citation)"
              >
                <FileCode :size="14" />
                <span v-if="citation.source_type === 'code'">[{{ index + 1 }}] {{ repositoryMap.get(citation.repo)?.name ?? citation.repo }}/{{ citation.path }} L{{ citation.start_line }}–{{ citation.end_line }}</span>
                <span v-else>
                  [{{ index + 1 }}] {{ citation.source_type === 'wiki' ? 'Wiki' : '文档' }} · {{ citation.title }}
                  <template v-if="citation.external_provider"> · {{ citation.external_provider }}</template>
                  <template v-if="citation.section"> · {{ citation.section }}</template>
                  <template v-if="citation.sheet">
                    · 工作表 {{ citation.sheet }}
                    <template v-if="citation.row_start"> · 行 {{ citation.row_start }}–{{ citation.row_end ?? citation.row_start }}</template>
                  </template>
                  <template v-else-if="citation.slide"> · 第 {{ citation.slide }} 张幻灯片</template>
                  <template v-else-if="citation.page"> · 第 {{ citation.page }} 页</template>
                  <template v-if="citation.source_type === 'wiki' && citation.sources?.length"> · {{ citation.sources.length }} 条来源</template>
                </span>
              </button>
            </div>
          </div>
        </article>

        <article v-if="sending" class="chat-message assistant">
          <span class="chat-avatar" aria-hidden="true"><Bot :size="17" /></span>
          <div class="chat-bubble"><div class="loading-block compact-loading"><div class="loading-spinner" /><span>正在检索并生成回答…</span></div></div>
        </article>
        <div v-if="chatError" class="error-banner">{{ chatError }}</div>
      </section>

      <form class="chat-composer" @submit.prevent="ask()">
        <textarea v-model="draft" rows="2" placeholder="输入问题；Enter发送，Shift+Enter换行" aria-label="提问内容" :disabled="chatStatus.data.value?.enabled === false" @keydown.enter.exact.prevent="ask()" />
        <button class="command-button" type="submit" :disabled="!draft.trim() || sending || chatStatus.data.value?.enabled === false"><SendHorizontal :size="17" />提问</button>
      </form>
    </main>

    <aside class="chat-account-panel chat-memory-panel" :class="{ open: showMemory }">
      <header class="chat-panel-header">
        <div><Brain :size="17" /><strong>长期记忆</strong></div>
        <button class="icon-button mobile-panel-close" type="button" aria-label="关闭长期记忆" @click="showMemory = false"><X :size="17" /></button>
      </header>
      <p class="chat-memory-intro">只保存长期有用且由你确认的事实。新对话会自动使用这些记忆。</p>
      <form class="memory-create-form" @submit.prevent="createMemory">
        <select v-model="memoryForm.kind" aria-label="记忆类型">
          <option v-for="kind in memoryKinds" :key="kind.value" :value="kind.value">{{ kind.label }}</option>
        </select>
        <textarea v-model="memoryForm.content" rows="3" maxlength="1000" placeholder="例如：我偏好用中文解释调用链" aria-label="记忆内容" />
        <button class="secondary-button" type="submit" :disabled="!memoryForm.content.trim()"><Plus :size="15" />添加记忆</button>
      </form>
      <div v-if="memoryError" class="error-banner compact-banner">{{ memoryError }}</div>
      <div class="memory-list">
        <p v-if="memories.isPending.value" class="chat-panel-empty">正在加载…</p>
        <p v-else-if="!memories.data.value?.length" class="chat-panel-empty">还没有长期记忆</p>
        <article v-for="item in memories.data.value" :key="item.id" class="memory-card">
          <span class="memory-kind">{{ memoryKinds.find((kind) => kind.value === item.kind)?.label ?? item.kind }}</span>
          <p>{{ item.content }}</p>
          <button class="icon-button danger" type="button" aria-label="删除记忆" @click="deleteMemory(item)"><Trash2 :size="14" /></button>
        </article>
      </div>
      <p class="memory-security-note">密码、API Key和Token会被拒绝保存。删除账号时，记忆与对话会一并清除。</p>
    </aside>

    <button v-if="showHistory || showMemory" class="chat-panel-scrim" type="button" aria-label="关闭侧面板" @click="showHistory = false; showMemory = false" />
    <CodePreview v-if="previewResult" :result="previewResult" @close="previewResult = null" />
  </div>
</template>
