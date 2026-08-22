<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { Boxes, CheckCircle2, Database, KeyRound } from 'lucide-vue-next'
import { computed } from 'vue'

import { api } from '@/api'
import { useAuth } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDate, formatNumber } from '@/format'
import type { ApiToken, IndexJob, Repository, Stats } from '@/types'

const { isAdmin } = useAuth()

const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})
const jobs = useQuery({
  queryKey: ['index-jobs'],
  queryFn: async () => (await api.get<IndexJob[]>('/index-jobs')).data,
  refetchInterval: 5000,
})
const tokens = useQuery({
  queryKey: ['tokens'],
  queryFn: async () => (await api.get<ApiToken[]>('/tokens')).data,
  enabled: isAdmin,
  retry: false,
})
const stats = useQuery({
  queryKey: ['stats'],
  queryFn: async () => (await api.get<Stats>('/stats')).data,
})

const chunks = computed(() =>
  (repositories.data.value ?? []).reduce((total, repo) => total + repo.chunk_count, 0),
)
const successfulJobs = computed(
  () => (jobs.data.value ?? []).filter((job) => job.status === 'succeeded').length,
)
const maxLanguageChunks = computed(() =>
  Math.max(1, ...(stats.data.value?.languages.map((item) => item.chunks) ?? [1])),
)
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div>
        <p class="eyebrow">OPERATIONS</p>
        <h1>概览</h1>
      </div>
      <span class="live-indicator"><span />服务正常</span>
    </section>

    <section class="metric-grid">
      <div class="metric-item">
        <Database :size="19" />
        <span>仓库</span>
        <strong>{{ repositories.data.value?.length ?? 0 }}</strong>
      </div>
      <div class="metric-item">
        <Boxes :size="19" />
        <span>代码块</span>
        <strong>{{ formatNumber(chunks) }}</strong>
      </div>
      <div class="metric-item">
        <CheckCircle2 :size="19" />
        <span>成功任务</span>
        <strong>{{ successfulJobs }}</strong>
      </div>
      <div class="metric-item">
        <KeyRound :size="19" />
        <span>有效 Token</span>
        <strong>{{ tokens.data.value?.filter((token) => !token.revoked_at).length ?? '—' }}</strong>
      </div>
    </section>

    <section class="data-section">
      <div class="section-heading">
        <h2>语言分布</h2>
        <span>按代码块统计</span>
      </div>
      <div v-if="stats.data.value?.languages.length" class="language-bars">
        <div
          v-for="item in stats.data.value.languages"
          :key="item.language"
          class="language-row"
        >
          <span class="language-name">{{ item.language }}</span>
          <div class="language-track">
            <div
              class="language-fill"
              :style="{ width: `${(item.chunks / maxLanguageChunks) * 100}%` }"
            />
          </div>
          <span class="language-count">{{ formatNumber(item.chunks) }}</span>
        </div>
      </div>
      <EmptyState v-else title="暂无索引数据" />
    </section>

    <section class="data-section">
      <div class="section-heading">
        <h2>最近索引任务</h2>
        <RouterLink to="/jobs">查看全部</RouterLink>
      </div>
      <div class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr><th>状态</th><th>仓库</th><th>进度</th><th>提交</th><th>创建时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="job in jobs.data.value?.slice(0, 6)" :key="job.id">
              <td><StatusBadge :status="job.status" /></td>
              <td class="mono-cell">{{ job.repository_id.slice(0, 10) }}</td>
              <td>{{ job.progress }}%</td>
              <td class="mono-cell">{{ job.commit.slice(0, 8) || '—' }}</td>
              <td>{{ formatDate(job.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
