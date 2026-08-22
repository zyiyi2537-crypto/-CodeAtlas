<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  ArrowUp,
  Copy,
  FileCode,
  Folder,
} from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'

import { api } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import { formatNumber, shortCommit } from '@/format'
import type { FilePreview, Repository, TreeEntry, TreeResponse } from '@/types'

const repositoryId = ref('')
const currentPath = ref('')
const selectedFile = ref('')

const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})

const readyRepositories = computed(() =>
  (repositories.data.value ?? []).filter((repo) => repo.status === 'ready'),
)

watch(readyRepositories, (repos) => {
  const first = repos[0]
  if (!repositoryId.value && first) repositoryId.value = first.id
})

watch(repositoryId, () => {
  currentPath.value = ''
  selectedFile.value = ''
})

const tree = useQuery({
  queryKey: computed(() => ['tree', repositoryId.value, currentPath.value]),
  queryFn: async () =>
    (
      await api.get<TreeResponse>(`/repositories/${repositoryId.value}/tree`, {
        params: { path: currentPath.value },
      })
    ).data,
  enabled: computed(() => Boolean(repositoryId.value)),
})

const filePreview = useQuery({
  queryKey: computed(() => ['browse-file', repositoryId.value, selectedFile.value]),
  queryFn: async () =>
    (
      await api.get<FilePreview>(`/repositories/${repositoryId.value}/file`, {
        params: { path: selectedFile.value, start_line: 1, end_line: 400 },
      })
    ).data,
  enabled: computed(() => Boolean(repositoryId.value && selectedFile.value)),
})

const breadcrumbs = computed(() => {
  const parts = currentPath.value ? currentPath.value.split('/') : []
  const crumbs = [{ label: '根目录', path: '' }]
  parts.forEach((part, index) => {
    crumbs.push({ label: part, path: parts.slice(0, index + 1).join('/') })
  })
  return crumbs
})

const parentPath = computed(() => {
  if (!currentPath.value) return ''
  const parts = currentPath.value.split('/')
  return parts.slice(0, -1).join('/')
})

function openEntry(entry: TreeEntry) {
  if (entry.type === 'dir') {
    currentPath.value = entry.path
    selectedFile.value = ''
  } else {
    selectedFile.value = entry.path
  }
}

async function copyFile() {
  if (filePreview.data.value?.content) {
    await navigator.clipboard.writeText(filePreview.data.value.content)
  }
}
</script>

<template>
  <div class="page-container browse-page">
    <section class="page-heading">
      <div>
        <p class="eyebrow">CODE BROWSER</p>
        <h1>代码浏览</h1>
      </div>
      <label class="chat-scope">
        <span>仓库</span>
        <select v-model="repositoryId" aria-label="选择仓库">
          <option v-for="repo in readyRepositories" :key="repo.id" :value="repo.id">
            {{ repo.name }}
          </option>
        </select>
      </label>
    </section>

    <EmptyState
      v-if="!readyRepositories.length && !repositories.isPending.value"
      title="暂无可浏览的仓库"
      description="仓库完成索引后即可在此浏览目录与文件。"
    />

    <div v-else class="browse-layout">
      <section class="browse-tree panel">
        <nav class="breadcrumbs" aria-label="路径">
          <template v-for="(crumb, index) in breadcrumbs" :key="crumb.path">
            <button type="button" class="crumb" @click="currentPath = crumb.path">
              {{ crumb.label }}
            </button>
            <span v-if="index < breadcrumbs.length - 1" aria-hidden="true">/</span>
          </template>
        </nav>

        <div v-if="tree.isPending.value" class="loading-block">
          <div class="loading-spinner" />
          <span>正在读取目录…</span>
        </div>
        <div v-else-if="tree.error.value" class="error-banner">目录读取失败</div>
        <ul v-else class="tree-list">
          <li v-if="currentPath">
            <button type="button" class="tree-row" @click="currentPath = parentPath">
              <ArrowUp :size="16" />
              <span>..</span>
            </button>
          </li>
          <li v-for="entry in tree.data.value?.entries ?? []" :key="entry.path">
            <button
              type="button"
              class="tree-row"
              :class="{ selected: selectedFile === entry.path }"
              @click="openEntry(entry)"
            >
              <Folder v-if="entry.type === 'dir'" :size="16" />
              <FileCode v-else :size="16" />
              <span class="tree-name">{{ entry.name }}</span>
              <span v-if="entry.size != null" class="tree-size">
                {{ formatNumber(entry.size) }} B
              </span>
            </button>
          </li>
        </ul>
      </section>

      <section class="browse-content panel">
        <EmptyState
          v-if="!selectedFile"
          title="选择左侧文件查看内容"
          description="点击目录可进入，点击文件在此预览。"
        />
        <div v-else-if="filePreview.isPending.value" class="loading-block">
          <div class="loading-spinner" />
          <span>正在读取文件…</span>
        </div>
        <template v-else-if="filePreview.data.value">
          <header class="file-header">
            <div>
              <strong>{{ selectedFile }}</strong>
              <span>
                {{ shortCommit(filePreview.data.value.commit) }} ·
                L{{ filePreview.data.value.start_line }}–{{ filePreview.data.value.end_line }}
              </span>
            </div>
            <button
              class="icon-button tooltip"
              type="button"
              data-tooltip="复制内容"
              aria-label="复制内容"
              @click="copyFile"
            >
              <Copy :size="16" />
            </button>
          </header>
          <pre class="source-code"><code>{{ filePreview.data.value.content }}</code></pre>
        </template>
        <div v-else class="error-banner">文件读取失败</div>
      </section>
    </div>
  </div>
</template>
