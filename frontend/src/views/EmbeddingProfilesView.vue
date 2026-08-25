<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Check, Cpu, Plus, RefreshCw, TestTube2, X } from 'lucide-vue-next'
import { reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'

type Provider = 'openai' | 'tencent_multimodal'
interface Profile {
  id: string
  name: string
  base_url: string
  model: string
  dimension: number
  credential_ref: string
  credential_configured: boolean
  credential_env: string
  backend: string
  provider: Provider
  is_active: boolean
  queued_jobs?: number
}

const queryClient = useQueryClient()
const showCreate = ref(false)
const error = ref('')
const form = reactive({
  name: '',
  base_url: '',
  model: '',
  dimension: 1024,
  credential_ref: '',
  provider: 'openai' as Provider,
})
const profiles = useQuery({
  queryKey: ['embedding-profiles'],
  queryFn: async () => (await api.get<Profile[]>('/embedding-profiles')).data,
})
const create = useMutation({
  mutationFn: async () =>
    (await api.post('/embedding-profiles', form, { headers: csrfHeaders() })).data,
  onSuccess: async () => {
    showCreate.value = false
    Object.assign(form, {
      name: '',
      base_url: '',
      model: '',
      dimension: 1024,
      credential_ref: '',
      provider: 'openai',
    })
    await queryClient.invalidateQueries({ queryKey: ['embedding-profiles'] })
  },
  onError: (e) => {
    error.value = errorMessage(e)
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
  onError: (e) => {
    error.value = errorMessage(e)
  },
})
const probe = useMutation({
  mutationFn: async () =>
    (
      await api.post<{ dimension: number }>(
        '/embedding-profiles/probe',
        {
          base_url: form.base_url,
          model: form.model,
          credential_ref: form.credential_ref,
          provider: form.provider,
        },
        { headers: csrfHeaders() },
      )
    ).data,
  onSuccess: (data) => {
    form.dimension = data.dimension
    error.value = ''
  },
  onError: (e) => {
    error.value = errorMessage(e)
  },
})
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">EMBEDDING PROFILES</p><h1>Embedding 模型</h1></div>
      <button class="command-button" type="button" @click="showCreate = true">
        <Plus :size="16" />添加模型
      </button>
    </section>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <section class="data-section">
      <div class="section-heading">
        <h2>已配置模型</h2>
        <span>切换后会自动为现有仓库排队重建索引，旧向量不会混用</span>
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
            <small>{{ profile.base_url }} · 凭据：{{ profile.credential_configured ? '已配置' : '未配置' }}</small>
            <small v-if="!profile.credential_configured" class="error-text">
              服务器环境变量 {{ profile.credential_env }} 未配置
            </small>
          </span>
          <span class="source-card-meta">
            <span>{{ profile.backend }}</span>
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
          </span>
        </div>
      </div>
      <EmptyState v-else title="尚未配置 Embedding 模型" />
    </section>
    <div
      v-if="showCreate"
      class="preview-backdrop"
      role="presentation"
      @click.self="showCreate = false"
    >
      <section class="form-dialog" role="dialog" aria-modal="true">
        <header class="dialog-header">
          <h2>添加 Embedding 模型</h2>
          <button class="icon-button" type="button" aria-label="关闭" @click="showCreate = false">
            <X :size="18" />
          </button>
        </header>
        <form class="stack-form" @submit.prevent="create.mutate()">
          <label>
            <span>协议</span>
            <select v-model="form.provider">
              <option value="openai">OpenAI-compatible /embeddings</option>
              <option value="tencent_multimodal">腾讯 TokenHub /embeddings/multimodal</option>
            </select>
          </label>
          <label><span>名称</span><input v-model="form.name" required placeholder="tencent-kinfra" /></label>
          <label>
            <span>Base URL</span>
            <input v-model="form.base_url" type="url" required placeholder="https://tokenhub.tencentmaas.com/v1" />
          </label>
          <label><span>模型名</span><input v-model="form.model" required placeholder="kinfra-vl-embedding-2b" /></label>
          <label>
            <span>维度</span>
            <span class="inline-field">
              <input v-model.number="form.dimension" type="number" min="64" max="4096" required />
              <button
                class="secondary-button"
                type="button"
                :disabled="probe.isPending.value || !form.base_url || !form.model || !form.credential_ref"
                @click="probe.mutate()"
              >
                <TestTube2 :size="15" />探测维度
              </button>
            </span>
          </label>
          <label>
            <span>凭据引用</span>
            <input v-model="form.credential_ref" required placeholder="tencent-kinfra" />
          </label>
          <p class="form-hint">
            填写引用名称，不要填写 API Key。服务器需配置对应环境变量
            CODEATLAS_CREDENTIAL_TENCENT_KINFRA，密钥不会进入数据库。先探测真实向量维度，再保存并激活。
          </p>
          <div class="form-actions">
            <button class="secondary-button" type="button" @click="showCreate = false">取消</button>
            <button class="command-button" type="submit">保存</button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
