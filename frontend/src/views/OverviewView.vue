<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  GitCommitHorizontal,
  Layers3,
  Search,
} from 'lucide-vue-next'
import { computed } from 'vue'

import { api } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDate, formatNumber } from '@/format'
import type { IndexJob, Repository, Stats } from '@/types'

const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})
const jobs = useQuery({
  queryKey: ['index-jobs'],
  queryFn: async () => (await api.get<IndexJob[]>('/index-jobs')).data,
  refetchInterval: 5000,
})
const stats = useQuery({
  queryKey: ['stats'],
  queryFn: async () => (await api.get<Stats>('/stats')).data,
})

const repositoryList = computed(() => repositories.data.value ?? [])
const jobList = computed(() => jobs.data.value ?? [])
const repositoryNames = computed(
  () => new Map(repositoryList.value.map((repository) => [repository.id, repository.name])),
)
const chunks = computed(() =>
  repositoryList.value.reduce((total, repository) => total + repository.chunk_count, 0),
)
const readyRepositories = computed(() =>
  repositoryList.value.filter((repository) => repository.status === 'ready').length,
)
const activeJobs = computed(() =>
  jobList.value.filter((job) => job.status === 'queued' || job.status === 'running'),
)
const failedJobs = computed(() => jobList.value.filter((job) => job.status === 'failed').length)
const coverage = computed(() => {
  if (!repositoryList.value.length) return 0
  return Math.round((readyRepositories.value / repositoryList.value.length) * 100)
})
const maxLanguageChunks = computed(() =>
  Math.max(1, ...(stats.data.value?.languages.map((item) => item.chunks) ?? [1])),
)
</script>

<template>
  <div class="page-container overview-page">
    <section class="page-heading overview-heading">
      <div>
        <p class="eyebrow">INDEX CONTROL / LIVE</p>
        <h1>索引控制图</h1>
        <p class="heading-note">代码资产、索引队列与检索覆盖状态</p>
      </div>
      <div class="overview-actions">
        <span class="live-indicator"><span />服务正常</span>
        <RouterLink class="command-button compact" to="/">
          <Search :size="15" />
          搜索代码
        </RouterLink>
      </div>
    </section>

    <section class="control-map" aria-label="索引运行概况">
      <div class="coverage-panel">
        <div class="panel-kicker">
          <span>索引覆盖</span>
          <span>{{ readyRepositories }}/{{ repositoryList.length }} READY</span>
        </div>
        <div class="coverage-reading">
          <strong>{{ coverage }}</strong>
          <span>%</span>
        </div>
        <div class="pulse-rail" aria-hidden="true">
          <span class="pulse-origin" />
          <span class="pulse-line" :style="{ width: `${coverage}%` }" />
          <span class="pulse-head" :style="{ left: `${coverage}%` }" />
        </div>
        <div class="repository-signal-list">
          <RouterLink
            v-for="repository in repositoryList.slice(0, 5)"
            :key="repository.id"
            class="repository-signal"
            :to="`/browse?repository=${repository.id}`"
          >
            <span class="signal-state" :class="`signal-${repository.status}`" />
            <strong>{{ repository.name }}</strong>
            <span>{{ formatNumber(repository.chunk_count) }} chunks</span>
            <ArrowUpRight :size="14" />
          </RouterLink>
          <EmptyState v-if="!repositoryList.length" title="尚未接入代码仓库" />
        </div>
      </div>

      <div class="queue-panel">
        <div class="panel-heading">
          <div>
            <span class="panel-index">Q</span>
            <h2>实时队列</h2>
          </div>
          <RouterLink to="/jobs" aria-label="查看全部索引任务">
            <ArrowUpRight :size="17" />
          </RouterLink>
        </div>

        <div v-if="activeJobs.length" class="active-job-list">
          <div v-for="job in activeJobs.slice(0, 3)" :key="job.id" class="active-job">
            <div class="active-job-title">
              <Activity v-if="job.status === 'running'" :size="15" />
              <Clock3 v-else :size="15" />
              <strong>{{ repositoryNames.get(job.repository_id) ?? job.repository_id.slice(0, 10) }}</strong>
              <span>{{ job.progress }}%</span>
            </div>
            <div class="queue-track"><span :style="{ width: `${job.progress}%` }" /></div>
            <small>{{ job.message || (job.status === 'queued' ? '等待执行' : '正在建立索引') }}</small>
          </div>
        </div>
        <div v-else class="queue-idle">
          <CheckCircle2 :size="25" />
          <strong>队列已清空</strong>
          <span>所有索引任务均已处理</span>
        </div>

        <div class="queue-foot">
          <span><i class="status-pin running" />{{ activeJobs.length }} 进行中</span>
          <span><i class="status-pin failed" />{{ failedJobs }} 失败</span>
        </div>
      </div>
    </section>

    <section class="telemetry-strip" aria-label="资产统计">
      <div>
        <Database :size="17" />
        <span>代码仓库</span>
        <strong>{{ repositoryList.length }}</strong>
        <small>REPOSITORIES</small>
      </div>
      <div>
        <Layers3 :size="17" />
        <span>可检索代码块</span>
        <strong>{{ formatNumber(chunks) }}</strong>
        <small>SEARCHABLE CHUNKS</small>
      </div>
      <div>
        <GitCommitHorizontal :size="17" />
        <span>已记录任务</span>
        <strong>{{ formatNumber(jobList.length) }}</strong>
        <small>INDEX RUNS</small>
      </div>
      <div :class="{ warning: failedJobs > 0 }">
        <CircleAlert :size="17" />
        <span>需处理异常</span>
        <strong>{{ failedJobs }}</strong>
        <small>FAILED RUNS</small>
      </div>
    </section>

    <section class="overview-data-grid">
      <div class="language-section">
        <div class="section-heading">
          <div>
            <span class="section-code">LANG</span>
            <h2>语言构成</h2>
          </div>
          <span>按代码块统计</span>
        </div>
        <div v-if="stats.data.value?.languages.length" class="language-spectrum">
          <div
            v-for="(item, index) in stats.data.value.languages.slice(0, 6)"
            :key="item.language"
            class="spectrum-row"
          >
            <span class="spectrum-rank">{{ String(index + 1).padStart(2, '0') }}</span>
            <strong>{{ item.language }}</strong>
            <div class="spectrum-track">
              <span :style="{ width: `${(item.chunks / maxLanguageChunks) * 100}%` }" />
            </div>
            <span class="spectrum-value">{{ formatNumber(item.chunks) }}</span>
          </div>
        </div>
        <EmptyState v-else title="暂无语言统计" />
      </div>

      <div class="recent-section">
        <div class="section-heading">
          <div>
            <span class="section-code">RUNS</span>
            <h2>最近任务</h2>
          </div>
          <RouterLink to="/jobs">查看全部</RouterLink>
        </div>
        <div v-if="jobList.length" class="recent-run-list">
          <RouterLink v-for="job in jobList.slice(0, 5)" :key="job.id" to="/jobs" class="recent-run">
            <StatusBadge :status="job.status" />
            <div>
              <strong>{{ repositoryNames.get(job.repository_id) ?? job.repository_id.slice(0, 10) }}</strong>
              <span>{{ formatDate(job.created_at) }}</span>
            </div>
            <code>{{ job.commit.slice(0, 7) || 'pending' }}</code>
          </RouterLink>
        </div>
        <EmptyState v-else title="暂无索引任务" />
      </div>
    </section>
  </div>
</template>
