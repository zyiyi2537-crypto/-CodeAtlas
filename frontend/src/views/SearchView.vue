<script setup lang="ts">
import { useMutation, useQuery } from '@tanstack/vue-query'
import {
  Braces,
  ChevronRight,
  FileCode,
  Filter,
  GitCommitHorizontal,
  Search,
  X,
} from 'lucide-vue-next'
import { computed, reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import CodePreview from '@/components/CodePreview.vue'
import EmptyState from '@/components/EmptyState.vue'
import { formatNumber, shortCommit } from '@/format'
import type { Repository, SearchResult } from '@/types'

const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})

const filters = reactive({
  query: '',
  repositoryId: '',
  language: '',
  pathPrefix: '',
})
const searchedQuery = ref('')
const filtersOpen = ref(false)
const selectedResult = ref<SearchResult | null>(null)

const searchMutation = useMutation({
  mutationFn: async () => {
    const { data } = await api.post<SearchResult[]>('/search', {
      query: filters.query,
      repository_ids: filters.repositoryId ? [filters.repositoryId] : [],
      languages: filters.language ? [filters.language] : [],
      path_prefix: filters.pathPrefix,
      limit: 10,
    })
    return data
  },
  onSuccess: () => {
    searchedQuery.value = filters.query.trim()
  },
})

const repositoryMap = computed(
  () => new Map((repositories.data.value ?? []).map((repo) => [repo.id, repo])),
)
const totalChunks = computed(() =>
  (repositories.data.value ?? []).reduce((total, repo) => total + repo.chunk_count, 0),
)

function submitSearch() {
  if (!filters.query.trim()) return
  searchMutation.mutate()
}
</script>

<template>
  <div class="search-page">
    <section class="search-heading page-heading">
      <div>
        <p class="eyebrow">CODE ATLAS · PRIVATE CODE KNOWLEDGE BASE</p>
        <h1>代码搜索</h1>
      </div>
      <div class="index-summary" aria-label="索引摘要">
        <span><strong>{{ repositories.data.value?.length ?? 0 }}</strong> repositories</span>
        <span><strong>{{ formatNumber(totalChunks) }}</strong> chunks</span>
      </div>
    </section>

    <section class="search-workspace">
      <form class="search-form" @submit.prevent="submitSearch">
        <Search class="search-leading-icon" :size="20" aria-hidden="true" />
        <input
          v-model="filters.query"
          type="search"
          name="query"
          autocomplete="off"
          placeholder="搜索符号、实现或错误信息"
          aria-label="搜索代码"
        />
        <button
          class="icon-button filter-button tooltip"
          :class="{ active: filtersOpen }"
          type="button"
          data-tooltip="筛选条件"
          aria-label="筛选条件"
          @click="filtersOpen = !filtersOpen"
        >
          <Filter :size="18" />
        </button>
        <button class="command-button search-submit" type="submit" :disabled="searchMutation.isPending.value">
          <Search :size="17" />
          {{ searchMutation.isPending.value ? '检索中' : '检索' }}
        </button>
      </form>

      <div v-if="filtersOpen" class="filter-row">
        <label>
          <span>仓库</span>
          <select v-model="filters.repositoryId">
            <option value="">全部仓库</option>
            <option v-for="repo in repositories.data.value" :key="repo.id" :value="repo.id">
              {{ repo.name }}
            </option>
          </select>
        </label>
        <label>
          <span>语言</span>
          <select v-model="filters.language">
            <option value="">全部语言</option>
            <option value="java">Java</option>
            <option value="python">Python</option>
            <option value="typescript">TypeScript</option>
            <option value="javascript">JavaScript</option>
          </select>
        </label>
        <label class="path-filter">
          <span>路径前缀</span>
          <input v-model="filters.pathPrefix" placeholder="src/main/" />
        </label>
        <button
          class="icon-button tooltip"
          type="button"
          data-tooltip="清除筛选"
          aria-label="清除筛选"
          @click="filters.repositoryId = ''; filters.language = ''; filters.pathPrefix = ''"
        >
          <X :size="17" />
        </button>
      </div>
    </section>

    <div v-if="searchMutation.error.value" class="error-banner">
      {{ errorMessage(searchMutation.error.value) }}
    </div>

    <div v-if="searchMutation.isPending.value" class="loading-block">
      <div class="loading-spinner" />
      <span>正在检索代码库…</span>
    </div>

    <section v-if="searchMutation.data.value" class="results-section">
      <div class="section-heading">
        <h2>{{ searchMutation.data.value.length }} 个结果</h2>
        <span>“{{ searchedQuery }}”</span>
      </div>
      <div v-if="searchMutation.data.value.length" class="result-list">
        <button
          v-for="result in searchMutation.data.value"
          :key="`${result.repo}-${result.path}-${result.start_line}`"
          class="result-row"
          type="button"
          @click="selectedResult = result"
        >
          <span class="file-icon"><FileCode :size="19" /></span>
          <span class="result-main">
            <span class="result-path">
              <strong>{{ result.symbol }}</strong>
              <span>{{ result.path }}</span>
            </span>
            <code>{{ result.snippet.split('\n').slice(-4).join('\n') }}</code>
            <span class="result-meta">
              <span><Braces :size="14" />{{ result.language }}</span>
              <span><GitCommitHorizontal :size="14" />{{ shortCommit(result.commit) }}</span>
              <span>L{{ result.start_line }}–{{ result.end_line }}</span>
              <span>{{ repositoryMap.get(result.repo)?.name ?? result.repo }}</span>
            </span>
          </span>
          <span class="result-score">
            <strong>{{ Math.round(result.score * 100) }}</strong>
            <span>{{ result.retrieval }}</span>
          </span>
          <ChevronRight :size="18" aria-hidden="true" />
        </button>
      </div>
      <EmptyState v-else title="没有匹配的代码" />
    </section>

    <section v-else class="repository-band">
      <div class="section-heading">
        <h2>已索引仓库</h2>
        <span>{{ repositories.isPending.value ? '加载中' : '当前可检索' }}</span>
      </div>
      <div class="repository-strip">
        <a
          v-for="repo in repositories.data.value"
          :key="repo.id"
          :href="repo.git_url"
          target="_blank"
          rel="noreferrer"
          class="repository-item"
        >
          <span>
            <strong>{{ repo.name }}</strong>
            <small>{{ repo.license_name || 'Open source' }}</small>
          </span>
          <span>{{ formatNumber(repo.chunk_count) }} chunks</span>
        </a>
      </div>
    </section>

    <CodePreview
      v-if="selectedResult"
      :result="selectedResult"
      @close="selectedResult = null"
    />
  </div>
</template>
