<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Cloud, Database, Play, Plus, RefreshCw, TestTube2, Trash2, X } from 'lucide-vue-next'
import { reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import { buildExternalSourcePayload, externalSourceProviders, type ExternalSourceForm } from '@/externalSources'
import { formatDate } from '@/format'
import type { ExternalSource } from '@/types'

interface Collection { id: string; name: string; description: string }

const queryClient = useQueryClient()
const showCreate = ref(false)
const message = ref('')
const formError = ref('')
const form = reactive<ExternalSourceForm>({
  name: '', provider: 'aws_s3', collection_id: '', credential_ref: '',
  poll_interval_seconds: 1800, bucket: '', prefix: '', region: '', endpoint_url: '',
  base_url: '', space_key: '', root_page_id: '', deployment: 'cloud',
})

const sources = useQuery({
  queryKey: ['external-sources'],
  queryFn: async () => (await api.get<ExternalSource[]>('/external-sources')).data,
  refetchInterval: 10_000,
})
const collections = useQuery({
  queryKey: ['document-collections'],
  queryFn: async () => (await api.get<Collection[]>('/document-collections')).data,
})

const createSource = useMutation({
  mutationFn: async () => (await api.post<ExternalSource>('/external-sources', buildExternalSourcePayload(form), { headers: csrfHeaders() })).data,
  onSuccess: async () => {
    showCreate.value = false
    message.value = '来源已保存。请先在服务器配置页面显示的环境变量，再测试连接。'
    Object.assign(form, { name: '', provider: 'aws_s3', collection_id: '', credential_ref: '', poll_interval_seconds: 1800, bucket: '', prefix: '', region: '', endpoint_url: '', base_url: '', space_key: '', root_page_id: '', deployment: 'cloud' })
    await queryClient.invalidateQueries({ queryKey: ['external-sources'] })
  },
  onError: (error) => { formError.value = errorMessage(error) },
})
const testSource = useMutation({
  mutationFn: async (id: string) => (await api.post(`/external-sources/${id}/test`, null, { headers: csrfHeaders(), timeout: 60_000 })).data,
  onSuccess: () => { message.value = '连接测试成功。' },
  onError: (error) => { message.value = errorMessage(error) },
})
const syncSource = useMutation({
  mutationFn: async (id: string) => (await api.post(`/external-sources/${id}/sync`, null, { headers: csrfHeaders() })).data,
  onSuccess: async () => {
    message.value = '同步任务已提交。页面会自动刷新状态。'
    await queryClient.invalidateQueries({ queryKey: ['external-sources'] })
  },
  onError: (error) => { message.value = errorMessage(error) },
})
const deleteSource = useMutation({
  mutationFn: async (id: string) => (await api.delete(`/external-sources/${id}`, { headers: csrfHeaders() })).data,
  onSuccess: async () => {
    message.value = '来源及其同步文档、向量和原文件已删除。'
    await queryClient.invalidateQueries({ queryKey: ['external-sources'] })
  },
  onError: (error) => { message.value = errorMessage(error) },
})

function label(provider: string) {
  return externalSourceProviders.find((item) => item.value === provider)?.label ?? provider
}

function requestDelete(id: string) {
  if (window.confirm('删除该来源会同时删除其同步的文档、检索片段、向量和原文件。确定继续吗？')) {
    deleteSource.mutate(id)
  }
}
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">EXTERNAL KNOWLEDGE SOURCES</p><h1>外部知识源</h1></div>
      <button class="command-button" type="button" @click="showCreate = true"><Plus :size="17" />添加数据源</button>
    </section>
    <div v-if="message" class="info-banner">{{ message }} <button class="icon-button" type="button" aria-label="关闭" @click="message = ''"><X :size="15" /></button></div>
    <section class="data-section">
      <div class="section-heading"><div><h2>已配置来源</h2><span>支持 AWS S3、腾讯云 COS、Notion 与 Confluence；Secret 只在服务器环境配置</span></div><button class="icon-button tooltip" type="button" data-tooltip="刷新" @click="sources.refetch()"><RefreshCw :size="17" /></button></div>
      <div v-if="sources.data.value?.length" class="source-card-grid">
        <article v-for="source in sources.data.value" :key="source.id" class="source-card">
          <span class="source-card-icon"><Cloud v-if="source.provider === 'tencent_cos'" :size="20" /><Database v-else :size="20" /></span>
          <span class="source-card-main"><strong>{{ source.name }}</strong><small>{{ label(source.provider) }} · {{ source.config.bucket || source.config.base_url || source.config.root_page_id }}</small><small>凭据：{{ source.credential_ref }} · 环境变量：<code>{{ source.credential_env }}</code></small></span>
          <span class="source-card-meta"><span :class="source.sync_status === 'failed' ? 'status-failed' : ['queued', 'syncing'].includes(source.sync_status) ? 'status-running' : 'status-ready'">{{ source.sync_status }}</span><small>{{ source.credential_configured ? '服务器凭据已配置' : '等待服务器配置凭据' }}</small><small>检查于 {{ formatDate(source.last_checked_at) }}</small></span>
          <span class="heading-actions"><button class="secondary-button" type="button" :disabled="testSource.isPending.value" @click="testSource.mutate(source.id)"><TestTube2 :size="15" />测试</button><button class="secondary-button" type="button" :disabled="syncSource.isPending.value || !source.credential_configured" @click="syncSource.mutate(source.id)"><Play :size="15" />立即同步</button><button class="icon-button tooltip" type="button" data-tooltip="删除来源及同步内容" :disabled="deleteSource.isPending.value || source.sync_status === 'syncing'" @click="requestDelete(source.id)"><Trash2 :size="15" /></button></span>
          <small v-if="source.last_error" class="error-text">{{ source.last_error }}</small>
        </article>
      </div>
      <EmptyState v-else title="尚未配置外部知识源" description="先建立文档集，然后添加对象存储或企业文档来源。" />
    </section>

    <div v-if="showCreate" class="preview-backdrop" role="presentation" @click.self="showCreate = false">
      <section class="form-dialog" role="dialog" aria-modal="true" aria-label="添加外部知识源">
        <header class="dialog-header"><div><p class="eyebrow">NEW EXTERNAL SOURCE</p><h2>添加外部知识源</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="showCreate = false"><X :size="18" /></button></header>
        <div v-if="formError" class="error-banner">{{ formError }}</div>
        <form class="stack-form" @submit.prevent="createSource.mutate()">
          <div class="form-grid"><label><span>来源名称</span><input v-model="form.name" required /></label><label><span>连接器</span><select v-model="form.provider"><option v-for="provider in externalSourceProviders" :key="provider.value" :value="provider.value">{{ provider.label }}</option></select></label></div>
          <div class="form-grid"><label><span>目标文档集</span><select v-model="form.collection_id" required><option disabled value="">请选择</option><option v-for="collection in collections.data.value" :key="collection.id" :value="collection.id">{{ collection.name }}</option></select></label><label><span>凭据引用</span><input v-model="form.credential_ref" required placeholder="例如 aws-docs" /></label></div>
          <template v-if="form.provider === 'aws_s3' || form.provider === 'tencent_cos'">
            <div class="form-grid"><label><span>Bucket</span><input v-model="form.bucket" required /></label><label><span>Region</span><input v-model="form.region" required placeholder="ap-southeast-1 / ap-shanghai" /></label></div>
            <div class="form-grid"><label><span>Prefix（可选）</span><input v-model="form.prefix" /></label><label v-if="form.provider === 'aws_s3'"><span>Endpoint URL（可选）</span><input v-model="form.endpoint_url" type="url" /></label></div>
          </template>
          <template v-else-if="form.provider === 'notion'">
            <label><span>根页面 ID（可选）</span><input v-model="form.root_page_id" placeholder="留空同步 Integration 可访问的页面" /></label>
          </template>
          <template v-else>
            <div class="form-grid"><label><span>Confluence Base URL</span><input v-model="form.base_url" type="url" required placeholder="https://company.atlassian.net/wiki" /></label><label><span>Space Key</span><input v-model="form.space_key" required placeholder="ENG" /></label></div>
            <div class="form-grid"><label><span>部署类型</span><select v-model="form.deployment"><option value="cloud">Cloud</option><option value="data_center">Data Center</option></select></label><label><span>根页面 ID（可选）</span><input v-model="form.root_page_id" /></label></div>
          </template>
          <label><span>检查间隔（秒）</span><input v-model.number="form.poll_interval_seconds" type="number" min="300" max="86400" /></label>
          <p class="form-note">本页面不接收 Access Key、Secret Key 或 Token。保存后，在服务器安全环境中配置系统提示的 <code>CODEATLAS_CREDENTIAL_*</code> JSON 变量。Notion 使用 token；Confluence Cloud 使用 email + api_token；Data Center 使用 personal_access_token。</p>
          <div class="form-actions"><button class="secondary-button" type="button" @click="showCreate = false">取消</button><button class="command-button" type="submit" :disabled="createSource.isPending.value">保存来源</button></div>
        </form>
      </section>
    </div>
  </div>
</template>
