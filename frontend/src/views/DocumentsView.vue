<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { FileText, FolderPlus, Upload, X } from 'lucide-vue-next'
import { ref, watchEffect } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders, useAuth } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import type { KnowledgeSpace } from '@/types'

interface Collection { id: string; name: string; description: string; space_id: string }
interface DocumentItem { id: string; title: string; status: string; version: number; chunk_count: number }

const queryClient = useQueryClient()
const { isAdmin } = useAuth()
const selectedCollection = ref('')
const showCreate = ref(false)
const collectionName = ref('')
const collectionDescription = ref('')
const collectionSpaceId = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const error = ref('')

const collections = useQuery({
  queryKey: ['document-collections'],
  queryFn: async () => (await api.get<Collection[]>('/document-collections')).data,
})
const spaces = useQuery({
  queryKey: ['spaces'],
  queryFn: async () => (await api.get<KnowledgeSpace[]>('/spaces')).data,
})

watchEffect(() => {
  if (!collectionSpaceId.value && spaces.data.value?.[0]) {
    collectionSpaceId.value = spaces.data.value[0].id
  }
})

const documents = useQuery({
  queryKey: ['documents', selectedCollection],
  queryFn: async () => (await api.get<DocumentItem[]>(`/document-collections/${selectedCollection.value}/documents`)).data,
  enabled: () => Boolean(selectedCollection.value),
})

const createCollection = useMutation({
  mutationFn: async () => (await api.post('/document-collections', {
    name: collectionName.value,
    description: collectionDescription.value,
    space_id: collectionSpaceId.value,
  }, { headers: csrfHeaders() })).data as Collection,
  onSuccess: async (data) => {
    showCreate.value = false
    selectedCollection.value = data.id
    collectionName.value = ''
    collectionDescription.value = ''
    collectionSpaceId.value = spaces.data.value?.[0]?.id ?? ''
    await queryClient.invalidateQueries({ queryKey: ['document-collections'] })
  },
  onError: (e) => { error.value = errorMessage(e) },
})

const uploadDocument = useMutation({
  mutationFn: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    const response = await api.post(`/document-collections/${selectedCollection.value}/documents`, form, { headers: csrfHeaders() })
    return response.data
  },
  onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['documents', selectedCollection.value] }),
  onError: (e) => { error.value = errorMessage(e) },
})

function chooseFile() { fileInput.value?.click() }
function closeCreateDialog() { showCreate.value = false }
function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file && selectedCollection.value) uploadDocument.mutate(file)
  if (fileInput.value) fileInput.value.value = ''
}
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">DOCUMENT KNOWLEDGE</p><h1>项目文档</h1></div>
      <div v-if="isAdmin" class="heading-actions">
        <button class="secondary-button" type="button" :disabled="!selectedCollection" @click="chooseFile"><Upload :size="16" />上传文档</button>
        <button class="command-button" type="button" @click="showCreate = true"><FolderPlus :size="16" />新建文档集</button>
      </div>
    </section>
    <input ref="fileInput" class="visually-hidden" type="file" accept=".md,.markdown,.txt,.csv,.docx,.xlsx,.pdf,.pptx" @change="onFileChange" />
    <div v-if="error" class="error-banner">{{ error }} <button class="icon-button" type="button" aria-label="关闭" @click="error = ''"><X :size="15" /></button></div>
    <section class="data-section">
      <div class="section-heading"><h2>文档集</h2><span>原文件保留，抽取内容用于检索</span></div>
      <div v-if="collections.error.value" class="error-banner" data-query-error>
        <span>{{ errorMessage(collections.error.value) }}</span>
        <button class="secondary-button" type="button" data-query-retry @click="collections.refetch()">重试</button>
      </div>
      <div v-else-if="collections.data.value?.length" class="source-card-grid">
        <button v-for="collection in collections.data.value" :key="collection.id" class="source-card" :class="{ selected: selectedCollection === collection.id }" type="button" @click="selectedCollection = collection.id">
          <span class="source-card-icon"><FileText :size="19" /></span><span class="source-card-main"><strong>{{ collection.name }}</strong><small>{{ collection.description || '暂无描述' }}</small></span>
        </button>
      </div>
      <EmptyState v-else title="暂无文档集" description="先建立一个文档集，再上传项目开发文档。" />
    </section>
    <section v-if="selectedCollection" class="data-section">
      <div class="section-heading"><h2>已上传文档</h2><span>支持 Markdown、TXT、CSV、DOCX、XLSX、文本 PDF、PPTX</span></div>
      <div v-if="documents.error.value" class="error-banner" data-document-error>
        <span>{{ errorMessage(documents.error.value) }}</span>
        <button class="secondary-button" type="button" @click="documents.refetch()">重试</button>
      </div>
      <div v-else-if="documents.data.value?.length" class="gitlab-project-list">
        <div v-for="document in documents.data.value" :key="document.id" class="gitlab-project-row"><FileText :size="18" /><span><strong>{{ document.title }}</strong><small>版本 {{ document.version }} · {{ document.chunk_count }} 个检索片段</small></span><span>{{ document.status }}</span></div>
      </div>
      <EmptyState v-else title="文档集为空" description="上传 Word、Excel、文本 PDF、PPT 或 Markdown 后，会按标题、表格、页、工作表和幻灯片结构建立语义索引。" />
    </section>
    <div v-if="showCreate" class="preview-backdrop" role="presentation" @click.self="closeCreateDialog"><section v-modal-dialog="closeCreateDialog" class="form-dialog" role="dialog" aria-modal="true" aria-label="新建文档集"><header class="dialog-header"><h2>新建文档集</h2><button class="icon-button" type="button" aria-label="关闭" @click="closeCreateDialog"><X :size="18" /></button></header><form class="stack-form" @submit.prevent="createCollection.mutate()"><label><span>名称</span><input v-model="collectionName" required /></label><label><span>知识空间</span><select v-model="collectionSpaceId" required><option v-for="space in spaces.data.value" :key="space.id" :value="space.id">{{ space.name }}</option></select></label><label><span>描述</span><textarea v-model="collectionDescription" rows="3" /></label><div v-if="spaces.error.value" class="error-banner" data-scope-error>{{ errorMessage(spaces.error.value) }}</div><div class="form-actions"><button class="secondary-button" type="button" @click="closeCreateDialog">取消</button><button class="command-button" type="submit" :disabled="!collectionName || !collectionSpaceId || !!spaces.error.value || createCollection.isPending.value">创建</button></div></form></section></div>
  </div>
</template>
