<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  CheckCircle2,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  ServerCog,
  TestTube2,
  Trash2,
  X,
} from 'lucide-vue-next'
import { nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import {
  buildLlmProviderPayload,
  buildLlmSyncPayload,
  createLlmProviderForm,
} from '@/providerCredentials'
import type { LlmModel, LlmProvider } from '@/types'

const emit = defineEmits<{ close: [] }>()
const queryClient = useQueryClient()
const editingProviderId = ref('')
const editingProviderActive = ref(false)
const editingKeyConfigured = ref(false)
const modelError = ref('')
const syncedModels = ref<LlmModel[]>([])
const providerForm = reactive(createLlmProviderForm())
const closeButton = ref<HTMLButtonElement | null>(null)
const previousBodyOverflow = document.body.style.overflow
const globalChrome: Array<{
  element: HTMLElement
  inert: boolean
  ariaHidden: string | null
}> = []

function closeDialog() {
  emit('close')
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeDialog()
}

onMounted(async () => {
  document.body.style.overflow = 'hidden'
  document.querySelectorAll<HTMLElement>('.topbar, .sidebar').forEach((element) => {
    globalChrome.push({
      element,
      inert: Boolean(element.inert),
      ariaHidden: element.getAttribute('aria-hidden'),
    })
    element.inert = true
    element.setAttribute('aria-hidden', 'true')
  })
  window.addEventListener('keydown', handleKeydown)
  await nextTick()
  closeButton.value?.focus()
})

onBeforeUnmount(() => {
  document.body.style.overflow = previousBodyOverflow
  globalChrome.forEach(({ element, inert, ariaHidden }) => {
    element.inert = inert
    if (ariaHidden === null) element.removeAttribute('aria-hidden')
    else element.setAttribute('aria-hidden', ariaHidden)
  })
  window.removeEventListener('keydown', handleKeydown)
})

const providers = useQuery({
  queryKey: ['llm-providers'],
  queryFn: async () => (await api.get<LlmProvider[]>('/llm/providers')).data,
})

const syncModels = useMutation({
  mutationFn: async () => (await api.post<{ models: LlmModel[]; count: number }>(
    '/llm/providers/sync',
    buildLlmSyncPayload(providerForm, editingProviderId.value || undefined),
    { headers: csrfHeaders() },
  )).data,
  onSuccess: (data) => {
    syncedModels.value = data.models
    providerForm.models = data.models
    if (!providerForm.model && data.models[0]) providerForm.model = data.models[0].id
    modelError.value = data.count ? '' : '上游没有返回可用模型'
  },
  onError: (error) => { modelError.value = errorMessage(error) },
})

const saveProvider = useMutation({
  mutationFn: async () => {
    const payload = buildLlmProviderPayload(providerForm)
    return editingProviderId.value
      ? (await api.patch<LlmProvider>(`/llm/providers/${editingProviderId.value}`, payload, { headers: csrfHeaders() })).data
      : (await api.post<LlmProvider>('/llm/providers', payload, { headers: csrfHeaders() })).data
  },
  onSuccess: async (provider) => {
    if (!editingProviderId.value) {
      await api.post(`/llm/providers/${provider.id}/activate`, null, { headers: csrfHeaders() })
    }
    resetForm()
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] }),
      queryClient.invalidateQueries({ queryKey: ['chat-status'] }),
    ])
  },
  onError: (error) => { modelError.value = errorMessage(error) },
})

const activateProvider = useMutation({
  mutationFn: async (id: string) =>
    (await api.post<LlmProvider>(`/llm/providers/${id}/activate`, null, { headers: csrfHeaders() })).data,
  onSuccess: async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] }),
      queryClient.invalidateQueries({ queryKey: ['chat-status'] }),
    ])
  },
  onError: (error) => { modelError.value = errorMessage(error) },
})

const testProvider = useMutation({
  mutationFn: async (id: string) =>
    (await api.post<{ models: LlmModel[]; count: number }>(`/llm/providers/${id}/test`, null, { headers: csrfHeaders() })).data,
  onSuccess: async (data) => {
    modelError.value = data.count ? '' : '上游没有返回可用模型'
    await queryClient.invalidateQueries({ queryKey: ['llm-providers'] })
  },
  onError: (error) => { modelError.value = errorMessage(error) },
})

const deleteProvider = useMutation({
  mutationFn: async (id: string) => api.delete(`/llm/providers/${id}`, { headers: csrfHeaders() }),
  onSuccess: async () => {
    resetForm()
    await queryClient.invalidateQueries({ queryKey: ['llm-providers'] })
  },
  onError: (error) => { modelError.value = errorMessage(error) },
})

function resetForm() {
  editingProviderId.value = ''
  editingProviderActive.value = false
  editingKeyConfigured.value = false
  Object.assign(providerForm, createLlmProviderForm())
  syncedModels.value = []
  modelError.value = ''
}

function editProvider(provider: LlmProvider) {
  editingProviderId.value = provider.id
  editingProviderActive.value = provider.is_active
  editingKeyConfigured.value = provider.api_key_configured
  Object.assign(providerForm, createLlmProviderForm(provider))
  syncedModels.value = [...provider.models]
  modelError.value = ''
}

function removeProvider(provider: LlmProvider) {
  if (provider.is_active || !window.confirm(`删除LLM配置“${provider.name}”？`)) return
  deleteProvider.mutate(provider.id)
}
</script>

<template>
  <div class="preview-backdrop model-settings-backdrop" role="presentation" @click.self="closeDialog">
    <section class="model-settings-dialog" role="dialog" aria-modal="true" aria-label="模型配置">
      <header class="model-settings-header">
        <div class="model-settings-title">
          <span class="model-settings-icon"><ServerCog :size="19" /></span>
          <div><h2>模型配置</h2><p>管理OpenAI兼容服务；密钥只写入后端加密存储。</p></div>
        </div>
        <button ref="closeButton" class="icon-button" type="button" aria-label="关闭模型配置" @click="closeDialog"><X :size="18" /></button>
      </header>

      <div class="model-settings-body">
        <section class="model-config-section">
          <div class="model-section-heading">
            <div><span>01</span><div><h3>连接设置</h3><p>{{ editingProviderId ? '正在编辑已保存配置' : '新增并启用一个模型服务' }}</p></div></div>
            <button v-if="editingProviderId" class="text-button" type="button" @click="resetForm">退出编辑</button>
          </div>

          <form class="model-config-form" @submit.prevent="saveProvider.mutate()">
            <label class="model-field">
              <span>配置名称</span>
              <input v-model="providerForm.name" placeholder="例如：生产DeepSeek" />
            </label>
            <label class="model-field">
              <span>OpenAI兼容Base URL</span>
              <input v-model="providerForm.base_url" type="url" required placeholder="https://api.example.com/v1" />
            </label>
            <label class="model-field">
              <span>{{ editingKeyConfigured ? '替换API Key（留空保留）' : 'API Key' }}</span>
              <div class="secure-input"><KeyRound :size="16" /><input v-model="providerForm.api_key" type="password" autocomplete="new-password" :placeholder="editingKeyConfigured ? '已配置；留空继续使用原密钥' : '仅通过HTTPS提交'" @input="providerForm.clear_api_key = false" /></div>
            </label>
            <label v-if="editingProviderId && editingKeyConfigured && !editingProviderActive" class="check-row model-clear-key">
              <input v-model="providerForm.clear_api_key" type="checkbox" @change="providerForm.api_key = ''" />
              <span>明确清除数据库中加密保存的API Key</span>
            </label>
            <div class="model-field model-model-field">
              <label class="grow"><span>模型</span><select v-if="syncedModels.length" v-model="providerForm.model"><option v-for="model in syncedModels" :key="model.id" :value="model.id">{{ model.name }}（{{ model.id }}）</option></select><input v-else v-model="providerForm.model" required placeholder="deepseek-chat" /></label>
              <button class="secondary-button model-sync-button" type="button" :disabled="syncModels.isPending.value || !providerForm.base_url || (!providerForm.api_key && !editingKeyConfigured)" @click="syncModels.mutate()"><RefreshCw :size="16" :class="{ spin: syncModels.isPending.value }" />同步模型</button>
            </div>
            <p class="model-security-note"><KeyRound :size="14" /><span>密钥不会回显。留空不会清除原值；所有明文只通过HTTPS发送，并使用Fernet加密保存。</span></p>
            <div v-if="modelError" class="error-banner compact-banner">{{ modelError }}</div>
            <footer class="model-form-actions">
              <button v-if="editingProviderId" class="secondary-button" type="button" :disabled="testProvider.isPending.value || !editingKeyConfigured" @click="testProvider.mutate(editingProviderId)"><TestTube2 :size="16" />测试配置</button>
              <button class="command-button" type="submit" :disabled="saveProvider.isPending.value || !providerForm.base_url || (!editingProviderId && !providerForm.api_key) || !providerForm.model"><Plus :size="16" />{{ editingProviderId ? '保存修改' : '保存并启用' }}</button>
            </footer>
          </form>
        </section>

        <section class="model-config-section model-saved-section">
          <div class="model-section-heading">
            <div><span>02</span><div><h3>已保存配置</h3><p>切换只影响后续新请求</p></div></div>
            <span class="model-provider-count">{{ providers.data.value?.length ?? 0 }}个</span>
          </div>
          <div class="model-provider-grid">
            <p v-if="providers.isPending.value" class="model-empty-state">正在加载模型配置…</p>
            <p v-else-if="!providers.data.value?.length" class="model-empty-state">还没有模型配置。完成上方连接设置后保存。</p>
            <article v-for="provider in providers.data.value" :key="provider.id" class="model-provider-card" :class="{ active: provider.is_active }">
              <header><div><strong>{{ provider.name }}</strong><span v-if="provider.is_active" class="provider-active-badge"><CheckCircle2 :size="13" />当前使用</span></div><button class="icon-button" type="button" :aria-label="`编辑 ${provider.name}`" @click="editProvider(provider)"><Pencil :size="15" /></button></header>
              <dl><div><dt>模型</dt><dd>{{ provider.model }}</dd></div><div><dt>地址</dt><dd>{{ provider.base_url }}</dd></div><div><dt>凭据</dt><dd>{{ provider.api_key_configured ? '已加密保存' : '未配置' }}</dd></div></dl>
              <footer v-if="!provider.is_active">
                <button class="secondary-button" type="button" :disabled="!provider.api_key_configured || activateProvider.isPending.value" @click="activateProvider.mutate(provider.id)">切换到此配置</button>
                <button class="icon-button danger" type="button" :aria-label="`删除 ${provider.name}`" :disabled="deleteProvider.isPending.value" @click="removeProvider(provider)"><Trash2 :size="15" /></button>
              </footer>
            </article>
          </div>
        </section>
      </div>
    </section>
  </div>
</template>
