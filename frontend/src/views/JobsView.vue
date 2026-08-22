<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { RotateCw } from 'lucide-vue-next'
import { computed } from 'vue'

import { api } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDate, shortCommit } from '@/format'
import type { IndexJob, Repository } from '@/types'

const jobs = useQuery({
  queryKey: ['index-jobs'],
  queryFn: async () => (await api.get<IndexJob[]>('/index-jobs')).data,
  refetchInterval: (query) => {
    const active = query.state.data?.some((job) => ['queued', 'running'].includes(job.status))
    return active ? 2000 : 10_000
  },
})
const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})
const names = computed(() =>
  new Map((repositories.data.value ?? []).map((repo) => [repo.id, repo.name])),
)
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">INDEX PIPELINE</p><h1>索引任务</h1></div>
      <button class="icon-button tooltip" type="button" data-tooltip="刷新" aria-label="刷新" @click="jobs.refetch()">
        <RotateCw :size="18" :class="{ spinning: jobs.isFetching.value }" />
      </button>
    </section>

    <section class="data-section">
      <div v-if="jobs.data.value?.length" class="job-list">
        <article v-for="job in jobs.data.value" :key="job.id" class="job-row">
          <div class="job-status"><StatusBadge :status="job.status" /></div>
          <div class="job-main">
            <strong>{{ names.get(job.repository_id) ?? job.repository_id.slice(0, 10) }}</strong>
            <span>{{ job.error || job.message || '等待执行' }}</span>
            <div class="progress-track" :aria-label="`进度 ${job.progress}%`">
              <span :style="{ width: `${job.progress}%` }" />
            </div>
          </div>
          <div class="job-meta">
            <code>{{ shortCommit(job.commit) }}</code>
            <span>{{ formatDate(job.created_at) }}</span>
          </div>
        </article>
      </div>
      <EmptyState v-else title="暂无索引任务" />
    </section>
  </div>
</template>
