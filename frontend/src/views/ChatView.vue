<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Bot, FileCode, Plus, RefreshCw, SendHorizonal, Settings2, Sparkles, UserRound, X } from 'lucide-vue-next'
import { computed, nextTick, reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders, useAuth } from '@/auth'
import CodePreview from '@/components/CodePreview.vue'
import EmptyState from '@/components/EmptyState.vue'
import type {
  ChatCitation,
  ChatResponse,
  ChatStatus,
  LlmModel,
  LlmProvider,
  Repository,
  SearchResult,
} from '@/types'

interface Message {
  role: 'user' | 'assistant'
  content: string
  citations?: ChatCitation[]
}

const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})

const chatStatus = useQuery({
  queryKey: ['chat-status'],
  queryFn: async () => (await api.get<ChatStatus>('/chat/status')).data,
})

const messages = ref<Message[]>([])
const draft = ref('')
const repositoryId = ref('')
const previewResult = ref<SearchResult | null>(null)
const messageList = ref<HTMLElement | null>(null)
const showModelSettings = ref(false)
const modelError = ref('')
const syncedModels = ref<LlmModel[]>([])
const { isAdmin } = useAuth()
const queryClient = useQueryClient()
const providerForm = reactive({ name: '', base_url: '', api_key: '', model: '' })

const providers = useQuery({
  queryKey: ['llm-providers'],
  enabled: computed(() => isAdmin.value),
  queryFn: async () => (await api.get<LlmProvider[]>('/llm/providers')).data,
})

const syncModels = useMutation({
  mutationFn: async () => (await api.post<{ models: LlmModel[]; count: number }>(
    '/llm/providers/sync',
    { base_url: providerForm.base_url, api_key: providerForm.api_key },
    { headers: csrfHeaders() },
  )).data,
  onSuccess: (data) => {
    syncedModels.value = data.models
    if (!providerForm.model && data.models[0]) providerForm.model = data.models[0].id
    modelError.value = data.count ? '' : '上游没有返回可用模型'
  },
  onError: (error) => { modelError.value = errorMessage(error) },
})

const saveProvider = useMutation({
  mutationFn: async () => (await api.post<LlmProvider>('/llm/providers', {
    ...providerForm,
    models: syncedModels.value,
  }, { headers: csrfHeaders() })).data,
  onSuccess: async (provider) => {
    await api.post(`/llm/providers/${provider.id}/activate`, null, { headers: csrfHeaders() })
    showModelSettings.value = false
    Object.assign(providerForm, { name: '', base_url: '', api_key: '', model: '' })
    syncedModels.value = []
    await queryClient.invalidateQueries({ queryKey: ['llm-providers'] })
    await chatStatus.refetch()
  },
  onError: (error) => { modelError.value = errorMessage(error) },
})

const activateProvider = useMutation({
  mutationFn: async (id: string) => (await api.post(`/llm/providers/${id}/activate`, null, { headers: csrfHeaders() })).data,
  onSuccess: async () => { await chatStatus.refetch(); await queryClient.invalidateQueries({ queryKey: ['llm-providers'] }) },
  onError: (error) => { modelError.value = errorMessage(error) },
})

const suggestionPrompts = [
  '这个项目的入口在哪里？整体结构是怎样的？',
  '权限校验是怎么实现的？',
  '数据库连接在哪里初始化？',
  '有没有处理错误的统一逻辑？',
]

const historyPayload = computed(() =>
  messages.value.slice(-6).map((message) => ({
    role: message.role,
    content: message.content,
  })),
)

const chatMutation = useMutation({
  mutationFn: async (question: string) => {
    const { data } = await api.post<ChatResponse>('/chat', {
      question,
      repository_ids: repositoryId.value ? [repositoryId.value] : [],
      history: historyPayload.value,
    })
    return data
  },
  onSuccess: async (data) => {
    messages.value.push({
      role: 'assistant',
      content: data.answer,
      citations: data.citations,
    })
    await scrollToBottom()
  },
})

const repositoryMap = computed(
  () => new Map((repositories.data.value ?? []).map((repo) => [repo.id, repo])),
)

async function scrollToBottom() {
  await nextTick()
  messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' })
}

async function ask(question?: string) {
  const text = (question ?? draft.value).trim()
  if (!text || chatMutation.isPending.value) return
  messages.value.push({ role: 'user', content: text })
  draft.value = ''
  await scrollToBottom()
  chatMutation.mutate(text)
}

function openCitation(citation: ChatCitation) {
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

function openModelSettings() {
  modelError.value = ''
  showModelSettings.value = true
}
</script>

<template>
  <div class="page-container chat-page">
    <section class="page-heading">
      <div>
        <p class="eyebrow">AI ASSISTANT</p>
        <h1>代码问答</h1>
      </div>
      <label class="chat-scope">
        <span>范围</span>
        <select v-model="repositoryId" aria-label="选择仓库范围">
          <option value="">全部仓库</option>
          <option v-for="repo in repositories.data.value" :key="repo.id" :value="repo.id">
            {{ repo.name }}
          </option>
        </select>
      </label>
      <button v-if="isAdmin" class="secondary-button" type="button" @click="openModelSettings">
        <Settings2 :size="16" />模型配置
      </button>
    </section>

    <div v-if="chatStatus.data.value && !chatStatus.data.value.enabled" class="error-banner">
      问答服务未配置。管理员需要在后端设置 CODEATLAS_LLM_BASE_URL 与 CODEATLAS_LLM_API_KEY 后重启服务。
    </div>

    <section ref="messageList" class="chat-thread">
      <EmptyState
        v-if="!messages.length && !chatMutation.isPending.value"
        title="向代码库提问"
        description="基于已索引的代码生成回答，并附上引用出处。"
      >
        <div class="suggestion-grid">
          <button
            v-for="prompt in suggestionPrompts"
            :key="prompt"
            class="suggestion-chip"
            type="button"
            @click="ask(prompt)"
          >
            <Sparkles :size="15" />
            {{ prompt }}
          </button>
        </div>
      </EmptyState>

      <article
        v-for="(message, index) in messages"
        :key="index"
        class="chat-message"
        :class="message.role"
      >
        <span class="chat-avatar" aria-hidden="true">
          <UserRound v-if="message.role === 'user'" :size="17" />
          <Bot v-else :size="17" />
        </span>
        <div class="chat-bubble">
          <p class="chat-text">{{ message.content }}</p>
          <div v-if="message.citations?.length" class="citation-list">
            <span class="citation-label">引用</span>
            <button
              v-for="(citation, citationIndex) in message.citations"
              :key="`${citation.path}-${citation.start_line}`"
              class="citation-chip"
              type="button"
              @click="openCitation(citation)"
            >
              <FileCode :size="14" />
              <span>
                [{{ citationIndex + 1 }}]
                {{ repositoryMap.get(citation.repo)?.name ?? citation.repo }}/{{ citation.path }}
                L{{ citation.start_line }}–{{ citation.end_line }}
              </span>
            </button>
          </div>
        </div>
      </article>

      <article v-if="chatMutation.isPending.value" class="chat-message assistant">
        <span class="chat-avatar" aria-hidden="true"><Bot :size="17" /></span>
        <div class="chat-bubble">
          <div class="loading-block compact-loading">
            <div class="loading-spinner" />
            <span>正在检索并生成回答…</span>
          </div>
        </div>
      </article>

      <div v-if="chatMutation.error.value" class="error-banner">
        {{ errorMessage(chatMutation.error.value) }}
      </div>
    </section>

    <form class="chat-composer" @submit.prevent="ask()">
      <textarea
        v-model="draft"
        rows="2"
        placeholder="用自然语言描述你的问题，例如：登录失败时做了哪些处理？"
        aria-label="提问内容"
        :disabled="chatStatus.data.value ? !chatStatus.data.value.enabled : false"
        @keydown.enter.exact.prevent="ask()"
      />
      <button
        class="command-button"
        type="submit"
        :disabled="!draft.trim() || chatMutation.isPending.value || chatStatus.data.value?.enabled === false"
      >
        <SendHorizonal :size="17" />
        提问
      </button>
    </form>

    <CodePreview
      v-if="previewResult"
      :result="previewResult"
      @close="previewResult = null"
    />

    <div v-if="showModelSettings" class="preview-backdrop" role="presentation" @click.self="showModelSettings = false">
      <section class="form-dialog" role="dialog" aria-modal="true" aria-label="模型配置">
        <header class="dialog-header">
          <div class="dialog-title"><Settings2 :size="20" /><h2>模型配置</h2></div>
          <button class="icon-button" type="button" aria-label="关闭" @click="showModelSettings = false"><X :size="18" /></button>
        </header>
        <div class="stack-form">
          <label><span>配置名称（可选）</span><input v-model="providerForm.name" placeholder="DeepSeek / Kimi / 本地模型" /></label>
          <label><span>OpenAI 兼容 Base URL</span><input v-model="providerForm.base_url" type="url" required placeholder="https://api.example.com/v1" /></label>
          <label><span>API Key</span><input v-model="providerForm.api_key" type="password" autocomplete="new-password" placeholder="仅保存加密后的密文" /></label>
          <div class="form-row">
            <label class="grow"><span>模型</span><select v-if="syncedModels.length" v-model="providerForm.model"><option v-for="model in syncedModels" :key="model.id" :value="model.id">{{ model.name }}（{{ model.id }}）</option></select><input v-else v-model="providerForm.model" required placeholder="deepseek-chat" /></label>
            <button class="secondary-button form-inline-button" type="button" :disabled="syncModels.isPending.value || !providerForm.base_url" @click="syncModels.mutate()"><RefreshCw :size="16" :class="{ spin: syncModels.isPending.value }" />同步上游模型</button>
          </div>
          <div v-if="modelError" class="error-banner">{{ modelError }}</div>
          <p class="form-hint">通过上游的 <code>GET /models</code> 自动读取模型。API Key 只发送到 CodeAtlas 后端，不会回显。</p>
          <div class="form-actions"><button class="secondary-button" type="button" @click="showModelSettings = false">取消</button><button class="command-button" type="button" :disabled="saveProvider.isPending.value || !providerForm.base_url || !providerForm.api_key || !providerForm.model" @click="saveProvider.mutate()"><Plus :size="16" />保存并切换</button></div>
        </div>
        <section v-if="providers.data.value?.length" class="model-provider-list">
          <h3>已保存配置</h3>
          <div v-for="provider in providers.data.value" :key="provider.id" class="model-provider-row">
            <span><strong>{{ provider.name }}</strong><small>{{ provider.model }} · {{ provider.base_url }}</small></span>
            <button class="secondary-button" type="button" :disabled="provider.is_active || activateProvider.isPending.value" @click="activateProvider.mutate(provider.id)">{{ provider.is_active ? '当前使用' : '切换' }}</button>
          </div>
        </section>
      </section>
    </div>
  </div>
</template>
