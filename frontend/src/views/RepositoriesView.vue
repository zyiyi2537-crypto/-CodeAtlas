<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Archive, Plus, RefreshCw, X } from 'lucide-vue-next'
import { reactive, ref, watchEffect } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders, useAuth } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDate, formatNumber, shortCommit } from '@/format'
import type { IndexJob, KnowledgeSpace, Repository } from '@/types'

const queryClient = useQueryClient()
const { isAdmin } = useAuth()
const showCreate = ref(false)
const formError = ref('')
const form = reactive({
  name: '',
  description: '',
  git_url: '',
  branch: 'main',
  visibility: 'private',
  space_id: '',
  license_name: '',
  license_url: '',
})

const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})
const spaces = useQuery({
  queryKey: ['spaces'],
  queryFn: async () => (await api.get<KnowledgeSpace[]>('/spaces')).data,
})

watchEffect(() => {
  if (!form.space_id && spaces.data.value?.[0]) form.space_id = spaces.data.value[0].id
})

const createRepository = useMutation({
  mutationFn: async () =>
    (await api.post<Repository>('/repositories', form, { headers: csrfHeaders() })).data,
  onSuccess: async () => {
    showCreate.value = false
    Object.assign(form, {
      name: '', description: '', git_url: '', branch: 'main', visibility: 'private',
      space_id: spaces.data.value?.[0]?.id ?? '',
      license_name: '', license_url: '',
    })
    await queryClient.invalidateQueries({ queryKey: ['repositories'] })
  },
  onError: (error) => { formError.value = errorMessage(error) },
})

const syncRepository = useMutation({
  mutationFn: async (id: string) =>
    (await api.post<IndexJob>(`/repositories/${id}/sync`, null, { headers: csrfHeaders() })).data,
  onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: ['repositories'] })
    await queryClient.invalidateQueries({ queryKey: ['index-jobs'] })
  },
})

const archiveRepository = useMutation({
  mutationFn: async (id: string) =>
    api.delete(`/repositories/${id}`, { headers: csrfHeaders() }),
  onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['repositories'] }),
})

function closeCreateDialog() {
  showCreate.value = false
}
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">SOURCES</p><h1>仓库</h1></div>
      <button v-if="isAdmin" class="command-button" type="button" @click="showCreate = true">
        <Plus :size="17" />新增仓库
      </button>
    </section>

    <div v-if="syncRepository.error.value" class="error-banner">
      {{ errorMessage(syncRepository.error.value) }}
    </div>

    <section class="data-section">
      <div v-if="repositories.error.value" class="error-banner" data-query-error>
        <span>{{ errorMessage(repositories.error.value) }}</span>
        <button class="secondary-button" type="button" data-query-retry @click="repositories.refetch()">重试</button>
      </div>
      <div v-else-if="repositories.data.value?.length" class="data-table-wrap">
        <table class="data-table repository-table">
          <thead>
            <tr><th>仓库</th><th>状态</th><th>可见性</th><th>索引</th><th>提交</th><th>最近更新</th><th v-if="isAdmin"></th></tr>
          </thead>
          <tbody>
            <tr v-for="repo in repositories.data.value" :key="repo.id">
              <td>
                <a :href="repo.git_url" target="_blank" rel="noreferrer"><strong>{{ repo.name }}</strong></a>
                <small>{{ repo.description || repo.git_url }}</small>
              </td>
              <td><StatusBadge :status="repo.status" /></td>
              <td>{{ repo.visibility }}</td>
              <td>{{ formatNumber(repo.chunk_count) }}</td>
              <td class="mono-cell">{{ shortCommit(repo.last_commit) }}</td>
              <td>{{ formatDate(repo.last_indexed_at) }}</td>
              <td v-if="isAdmin" class="row-actions">
                <button class="icon-button tooltip" type="button" data-tooltip="同步仓库" aria-label="同步仓库" @click="syncRepository.mutate(repo.id)">
                  <RefreshCw :size="17" />
                </button>
                <button class="icon-button danger tooltip" type="button" data-tooltip="归档仓库" aria-label="归档仓库" @click="archiveRepository.mutate(repo.id)">
                  <Archive :size="17" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="暂无仓库" />
    </section>

    <div v-if="showCreate" class="preview-backdrop" role="presentation" @click.self="closeCreateDialog">
      <section v-modal-dialog="closeCreateDialog" class="form-dialog" role="dialog" aria-modal="true" aria-label="新增仓库">
        <header class="dialog-header">
          <div><p class="eyebrow">NEW SOURCE</p><h2>新增仓库</h2></div>
          <button class="icon-button" type="button" aria-label="关闭" @click="closeCreateDialog"><X :size="18" /></button>
        </header>
        <form class="stack-form two-column-form" @submit.prevent="createRepository.mutate()">
          <label><span>名称</span><input v-model="form.name" pattern="[a-z0-9][a-z0-9._-]+" required /></label>
          <label><span>分支</span><input v-model="form.branch" required /></label>
          <label class="full-span"><span>知识空间</span><select v-model="form.space_id" required><option v-for="space in spaces.data.value" :key="space.id" :value="space.id">{{ space.name }}</option></select></label>
          <label class="full-span"><span>Git HTTPS URL</span><input v-model="form.git_url" type="url" required /></label>
          <label class="full-span"><span>描述</span><textarea v-model="form.description" rows="3" /></label>
          <label><span>可见性</span><select v-model="form.visibility"><option value="public">public</option><option value="private">private</option></select></label>
          <label><span>许可证</span><input v-model="form.license_name" placeholder="Apache-2.0" /></label>
          <label class="full-span"><span>许可证 URL</span><input v-model="form.license_url" type="url" /></label>
          <div v-if="spaces.error.value" class="error-banner full-span" data-scope-error>{{ errorMessage(spaces.error.value) }}</div>
          <div v-if="formError" class="error-banner full-span">{{ formError }}</div>
          <div class="form-actions full-span">
            <button class="secondary-button" type="button" @click="closeCreateDialog">取消</button>
            <button class="command-button" type="submit" :disabled="createRepository.isPending.value || !form.space_id || !!spaces.error.value">创建仓库</button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
