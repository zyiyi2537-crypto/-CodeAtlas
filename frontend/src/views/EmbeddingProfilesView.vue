<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  Check,
  Cpu,
  Pencil,
  Plus,
  RefreshCw,
  TestTube2,
  Trash2,
  X,
} from 'lucide-vue-next'
import { computed, reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import {
  buildEmbeddingProfilePayload,
  buildEmbeddingProbePayload,
  createEmbeddingProfileForm,
  type EmbeddingProvider,
} from '@/embeddingProfiles'

interface Profile {
  id: string
  name: string
  base_url: string
  model: string
  dimension: number
  credential_ref: string
  credential_configured: boolean
  credential_source: 'encrypted' | 'server_ref' | 'none'
  credential_env: string
  backend: string
  provider: EmbeddingProvider
  is_active: boolean
  queued_jobs?: number
}

const queryClient = useQueryClient()
const showDialog = ref(false)
const editingProfileId = ref('')
const editingActive = ref(false)
const editingCredentialConfigured = ref(false)
const editingEncryptedCredential = ref(false)
const error = ref('')
const form = reactive(createEmbeddingProfileForm())

const profiles = useQuery({
  queryKey: ['embedding-profiles'],
  queryFn: async () => (await api.get<Profile[]>('/embedding-profiles')).data,
})

const save = useMutation({
  mutationFn: async () => {
    const id = editingProfileId.value
    const payload = buildEmbeddingProfilePayload(form, id || undefined)
    return id
      ? (await api.patch(`/embedding-profiles/${id}`, payload, { headers: csrfHeaders() })).data
      : (await api.post('/embedding-profiles', payload, { headers: csrfHeaders() })).data
  },
  onSuccess: async () => {
    closeDialog()
    await queryClient.invalidateQueries({ queryKey: ['embedding-profiles'] })
  },
  onError: (cause) => {
    error.value = errorMessage(cause)
  },
})

const activate = useMutation({
  mutationFn: async (id: string) =>
    (
      await api.post(`/embedding-profiles/${id}/activate`, null, {
        headers: csrfHeaders(),
      })
    ).data,
  onSuccess: async () => {
    error.value = ''
    await queryClient.invalidateQueries({ queryKey: ['embedding-profiles'] })
    await queryClient.invalidateQueries({ queryKey: ['index-jobs'] })
  },
  onError: (cause) => {
    error.value = errorMessage(cause)
  },
})

const probe = useMutation({
  mutationFn: async () =>
    (
      await api.post<{ dimension: number }>(
        '/embedding-profiles/probe',
        buildEmbeddingProbePayload(form, editingProfileId.value || undefined),
        { headers: csrfHeaders() },
      )
    ).data,
  onSuccess: (data) => {
    form.dimension = data.dimension
    error.value = ''
  },
  onError: (cause) => {
    error.value = errorMessage(cause)
  },
})

const remove = useMutation({
  mutationFn: async (id: string) =>
    api.delete(`/embedding-profiles/${id}`, { headers: csrfHeaders() }),
  onSuccess: async () => {
    error.value = ''
    await queryClient.invalidateQueries({ queryKey: ['embedding-profiles'] })
  },
  onError: (cause) => {
    error.value = errorMessage(cause)
  },
})

const canProbe = computed(() => Boolean(
  form.base_url
  && form.model
  && (form.api_key || form.credential_ref || editingCredentialConfigured.value),
))

function resetDialog() {
  editingProfileId.value = ''
  editingActive.value = false
  editingCredentialConfigured.value = false
  editingEncryptedCredential.value = false
  Object.assign(form, createEmbeddingProfileForm())
  error.value = ''
}

function openCreate() {
  resetDialog()
  showDialog.value = true
}

function openEdit(profile: Profile) {
  editingProfileId.value = profile.id
  editingActive.value = profile.is_active
  editingCredentialConfigured.value = profile.credential_configured
  editingEncryptedCredential.value = profile.credential_source === 'encrypted'
  Object.assign(form, {
    name: profile.name,
    base_url: profile.base_url,
    model: profile.model,
    dimension: profile.dimension,
    credential_ref: '',
    provider: profile.provider,
    api_key: '',
    clear_api_key: false,
  })
  error.value = ''
  showDialog.value = true
}

function closeDialog() {
  showDialog.value = false
  resetDialog()
}

function deleteProfile(profile: Profile) {
  if (profile.is_active || !window.confirm(`删除 Embedding 配置“${profile.name}”？`)) return
  remove.mutate(profile.id)
}

function credentialLabel(profile: Profile) {
  if (!profile.credential_configured) return '未配置'
  return profile.credential_source === 'encrypted' ? '已加密保存' : '服务器凭据'
}
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">EMBEDDING PROFILES</p><h1>Embedding 模型</h1></div>
      <button class="command-button" type="button" @click="openCreate">
        <Plus :size="16" />添加模型
      </button>
    </section>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <section class="data-section">
      <div class="section-heading">
        <h2>已配置模型</h2>
        <span>密钥经 HTTPS 提交并加密保存，切换模型会为现有知识重新构建隔离索引</span>
      </div>
      <div v-if="profiles.data.value?.length" class="source-card-grid">
        <div v-for="profile in profiles.data.value" :key="profile.id" class="source-card">
          <span class="source-card-icon"><Cpu :size="19" /></span>
          <span class="source-card-main">
            <strong>
              {{ profile.name }}
              <span v-if="profile.is_active" class="status-ready"><Check :size="13" />当前使用</span>
            </strong>
            <small>{{ profile.model }} · {{ profile.dimension }} dimensions · {{ profile.provider }}</small>
            <small>{{ profile.base_url }} · 凭据：{{ credentialLabel(profile) }}</small>
            <small v-if="!profile.credential_configured" class="error-text">
              请编辑配置并填写 API Key，或配置服务器凭据 {{ profile.credential_env }}
            </small>
          </span>
          <span class="source-card-meta">
            <span>{{ profile.backend }}</span>
            <span class="heading-actions">
              <button
                class="icon-button"
                type="button"
                :aria-label="`编辑 ${profile.name}`"
                @click="openEdit(profile)"
              >
                <Pencil :size="15" />
              </button>
              <button
                v-if="!profile.is_active"
                class="secondary-button"
                type="button"
                :disabled="activate.isPending.value || !profile.credential_configured"
                @click="activate.mutate(profile.id)"
              >
                <RefreshCw :size="15" />设为当前
              </button>
              <span v-else class="status-ready">已启用</span>
              <button
                v-if="!profile.is_active"
                class="icon-button danger"
                type="button"
                :aria-label="`删除 ${profile.name}`"
                :disabled="remove.isPending.value"
                @click="deleteProfile(profile)"
              >
                <Trash2 :size="15" />
              </button>
            </span>
          </span>
        </div>
      </div>
      <EmptyState v-else title="尚未配置 Embedding 模型" />
    </section>

    <div
      v-if="showDialog"
      class="preview-backdrop"
      role="presentation"
      @click.self="closeDialog"
    >
      <section v-modal-dialog="closeDialog" class="form-dialog" role="dialog" aria-modal="true" :aria-label="editingProfileId ? '编辑 Embedding 模型' : '添加 Embedding 模型'">
        <header class="dialog-header">
          <h2>{{ editingProfileId ? '编辑 Embedding 模型' : '添加 Embedding 模型' }}</h2>
          <button class="icon-button" type="button" aria-label="关闭" @click="closeDialog">
            <X :size="18" />
          </button>
        </header>
        <form class="stack-form" @submit.prevent="save.mutate()">
          <label>
            <span>协议</span>
            <select v-model="form.provider" :disabled="editingActive">
              <option value="openai">OpenAI-compatible /embeddings</option>
              <option value="tencent_multimodal">腾讯 TokenHub /embeddings/multimodal</option>
            </select>
          </label>
          <label><span>名称</span><input v-model="form.name" required placeholder="SiliconFlow BGE-M3" /></label>
          <label>
            <span>Base URL</span>
            <input v-model="form.base_url" type="url" required :disabled="editingActive" placeholder="https://api.siliconflow.cn/v1" />
          </label>
          <label>
            <span>模型名</span>
            <input v-model="form.model" required :disabled="editingActive" placeholder="BAAI/bge-m3" />
          </label>
          <label>
            <span>维度</span>
            <span class="inline-field">
              <input v-model.number="form.dimension" type="number" min="64" max="4096" required :disabled="editingActive" />
              <button
                class="secondary-button"
                type="button"
                :disabled="probe.isPending.value || !canProbe"
                @click="probe.mutate()"
              >
                <TestTube2 :size="15" />测试连接并探测维度
              </button>
            </span>
          </label>
          <label v-if="!editingProfileId">
            <span>服务器凭据引用（可选回退）</span>
            <input v-model="form.credential_ref" placeholder="siliconflow-embedding" />
          </label>
          <label>
            <span>{{ editingCredentialConfigured ? '替换 API Key（留空保留原值）' : 'API Key' }}</span>
            <input
              v-model="form.api_key"
              type="password"
              autocomplete="new-password"
              :placeholder="editingCredentialConfigured ? '留空则继续使用已保存的密钥' : '经 HTTPS 提交并加密保存'"
              @input="form.clear_api_key = false"
            />
          </label>
          <label v-if="editingProfileId && editingEncryptedCredential" class="check-row">
            <input v-model="form.clear_api_key" type="checkbox" @change="form.api_key = ''" />
            <span>明确清除数据库中加密保存的 API Key（活动配置必须有服务器凭据回退）</span>
          </label>
          <p class="form-hint">
            API Key 只会通过 HTTPS 发送到 CodeAtlas 后端并使用 Fernet 加密，前端和接口永不回显明文。
            活动模型的向量参数不能原地修改；请新建或编辑未启用配置后再切换。
          </p>
          <div class="form-actions">
            <button class="secondary-button" type="button" @click="closeDialog">取消</button>
            <button class="command-button" type="submit" :disabled="save.isPending.value">
              {{ editingProfileId ? '保存修改' : '保存配置' }}
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
