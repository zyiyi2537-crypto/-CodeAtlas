<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Cloud, FolderGit2, Plus, RefreshCw, X } from 'lucide-vue-next'
import { reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate } from '@/format'
import type { GitLabProject, GitLabSource } from '@/types'

const queryClient = useQueryClient()
const showCreate = ref(false)
const selectedSource = ref('')
const formError = ref('')
const form = reactive({
  name: '',
  base_url: 'https://gitlab.company.com',
  group_path: '',
  credential_ref: '',
  poll_interval_seconds: 1800,
})

const sources = useQuery({
  queryKey: ['gitlab-sources'],
  queryFn: async () => (await api.get<GitLabSource[]>('/gitlab-sources')).data,
})

const projects = useQuery({
  queryKey: ['gitlab-projects', selectedSource],
  queryFn: async () =>
    (await api.get<GitLabProject[]>(`/gitlab-sources/${selectedSource.value}/projects`)).data,
  enabled: () => Boolean(selectedSource.value),
})

const createSource = useMutation({
  mutationFn: async () =>
    (
      await api.post<GitLabSource>('/gitlab-sources', form, {
        headers: csrfHeaders(),
      })
    ).data,
  onSuccess: async (source) => {
    showCreate.value = false
    selectedSource.value = source.id
    Object.assign(form, {
      name: '',
      base_url: 'https://gitlab.company.com',
      group_path: '',
      credential_ref: '',
      poll_interval_seconds: 1800,
    })
    await queryClient.invalidateQueries({ queryKey: ['gitlab-sources'] })
  },
  onError: (error) => {
    formError.value = errorMessage(error)
  },
})

const importProject = useMutation({
  mutationFn: async (project: GitLabProject) =>
    (
      await api.post(
        `/gitlab-sources/${selectedSource.value}/import`,
        { external_project_id: project.external_id, visibility: 'private' },
        { headers: csrfHeaders() },
      )
    ).data,
  onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: ['repositories'] })
    await queryClient.invalidateQueries({ queryKey: ['gitlab-projects', selectedSource.value] })
  },
})

function selectSource(source: GitLabSource) {
  selectedSource.value = source.id
}

function closeCreateDialog() {
  showCreate.value = false
}
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div>
        <p class="eyebrow">GITLAB SOURCES</p>
        <h1>GitLab 代码源</h1>
      </div>
      <button class="command-button" type="button" @click="showCreate = true">
        <Plus :size="17" />
        添加 GitLab Group
      </button>
    </section>

    <div v-if="sources.error.value" class="error-banner">
      {{ errorMessage(sources.error.value) }}
    </div>

    <section class="data-section">
      <div class="section-heading">
        <h2>已配置来源</h2>
        <span>凭据只保存为引用，不在页面显示 Token</span>
      </div>
      <div v-if="sources.data.value?.length" class="source-card-grid">
        <button
          v-for="source in sources.data.value"
          :key="source.id"
          class="source-card"
          :class="{ selected: selectedSource === source.id }"
          type="button"
          @click="selectSource(source)"
        >
          <span class="source-card-icon"><Cloud :size="20" /></span>
          <span class="source-card-main">
            <strong>{{ source.name }}</strong>
            <small>{{ source.base_url }}/{{ source.group_path }}</small>
            <small>凭据引用：{{ source.credential_ref }}</small>
          </span>
          <span class="source-card-meta">
            <span :class="source.enabled ? 'status-ready' : 'status-muted'">
              {{ source.enabled ? '已启用' : '已停用' }}
            </span>
            <small>检查于 {{ formatDate(source.last_checked_at) }}</small>
          </span>
        </button>
      </div>
      <EmptyState v-else title="尚未配置 GitLab 来源" description="添加一个 Group 后即可发现项目并导入索引。" />
    </section>

    <section v-if="selectedSource" class="data-section">
      <div class="section-heading">
        <div>
          <h2>Group 项目</h2>
          <span>当前阶段先发现项目，下一步接入批量导入和自动同步</span>
        </div>
        <button
          class="icon-button tooltip"
          type="button"
          data-tooltip="刷新项目"
          aria-label="刷新项目"
          @click="projects.refetch()"
        >
          <RefreshCw :size="17" />
        </button>
      </div>
      <div v-if="projects.isPending.value" class="loading-block">
        <div class="loading-spinner" />
        <span>正在读取 GitLab 项目…</span>
      </div>
      <div v-else-if="projects.error.value" class="error-banner">
        {{ errorMessage(projects.error.value) }}
      </div>
      <div v-else-if="projects.data.value?.length" class="gitlab-project-list">
        <div
          v-for="project in projects.data.value"
          :key="project.external_id"
          class="gitlab-project-row"
        >
          <FolderGit2 :size="18" />
          <a
            class="gitlab-project-link"
            :href="project.web_url"
            target="_blank"
            rel="noreferrer"
          >
            <strong>{{ project.path_with_namespace }}</strong>
            <small>{{ project.description || '暂无描述' }}</small>
          </a>
          <span class="mono-cell">{{ project.default_branch }}</span>
          <button
            class="secondary-button gitlab-project-import"
            type="button"
            :disabled="importProject.isPending.value"
            @click="importProject.mutate(project)"
          >
            导入代码库
          </button>
        </div>
      </div>
      <EmptyState v-else title="Group 中没有项目" />
    </section>

    <div v-if="showCreate" class="preview-backdrop" role="presentation" @click.self="closeCreateDialog">
      <section v-modal-dialog="closeCreateDialog" class="form-dialog" role="dialog" aria-modal="true" aria-label="添加 GitLab Group">
        <header class="dialog-header">
          <div><p class="eyebrow">NEW GITLAB SOURCE</p><h2>添加 GitLab Group</h2></div>
          <button class="icon-button" type="button" aria-label="关闭" @click="closeCreateDialog"><X :size="18" /></button>
        </header>
        <form class="stack-form two-column-form" @submit.prevent="createSource.mutate()">
          <label><span>来源名称</span><input v-model="form.name" placeholder="company-gitlab" required /></label>
          <label><span>Group 路径</span><input v-model="form.group_path" placeholder="platform/backend" required /></label>
          <label class="full-span"><span>GitLab 地址</span><input v-model="form.base_url" type="url" required /></label>
          <label><span>凭据引用</span><input v-model="form.credential_ref" placeholder="gitlab-platform-readonly" required /></label>
          <label><span>检查间隔（秒）</span><input v-model.number="form.poll_interval_seconds" type="number" min="300" max="86400" required /></label>
          <p class="form-hint full-span">Token 不在此页面填写。服务端通过 CODEATLAS_CREDENTIAL_凭据引用读取，例如 CODEATLAS_CREDENTIAL_GITLAB_PLATFORM_READONLY。</p>
          <div v-if="formError" class="error-banner full-span">{{ formError }}</div>
          <div class="form-actions full-span">
            <button class="secondary-button" type="button" @click="closeCreateDialog">取消</button>
            <button class="command-button" type="submit" :disabled="createSource.isPending.value">保存来源</button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
