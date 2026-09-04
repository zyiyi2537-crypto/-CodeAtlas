<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { FileCode2, Pencil, Plus, RotateCcw, ScrollText, Trash2, X } from 'lucide-vue-next'
import { computed, reactive, ref, watchEffect } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders, useAuth } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDate, shortCommit } from '@/format'
import type {
  CompanyConvention,
  ConventionCitation,
  KnowledgeSpace,
  Repository,
} from '@/types'

interface ConventionForm {
  space_id: string
  title: string
  category: string
  language: string
  framework: string
  task: string
  rule: string
  prohibited_pattern: string
  examples_text: string
  status: CompanyConvention['status']
  citations: ConventionCitation[]
}

const queryClient = useQueryClient()
const { isAdmin } = useAuth()
const showEditor = ref(false)
const editingId = ref<string | null>(null)
const selectedSpace = ref('')
const filters = reactive({ language: '', framework: '', task: '' })

function emptyCitation(): ConventionCitation {
  return {
    repository_id: '',
    commit: '',
    path: '',
    symbol: '',
    start_line: 1,
    end_line: 1,
  }
}

const form = reactive<ConventionForm>({
  space_id: '',
  title: '',
  category: 'architecture',
  language: '',
  framework: '',
  task: '',
  rule: '',
  prohibited_pattern: '',
  examples_text: '',
  status: 'draft',
  citations: [emptyCitation()],
})

const spaces = useQuery({
  queryKey: ['spaces'],
  queryFn: async () => (await api.get<KnowledgeSpace[]>('/spaces')).data,
})

watchEffect(() => {
  if (!selectedSpace.value && spaces.data.value?.[0]) {
    selectedSpace.value = spaces.data.value[0].id
  }
})

const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})
const repositoriesInSpace = computed(() =>
  (repositories.data.value ?? []).filter((repository) => repository.space_id === form.space_id),
)
const repositoryById = computed(
  () => new Map((repositories.data.value ?? []).map((repository) => [repository.id, repository])),
)

const conventions = useQuery({
  queryKey: ['company-conventions', selectedSpace, filters],
  queryFn: async () => (
    await api.get<CompanyConvention[]>('/company-conventions', {
      params: {
        space_id: selectedSpace.value || undefined,
        language: filters.language || undefined,
        framework: filters.framework || undefined,
        task: filters.task || undefined,
      },
    })
  ).data,
  enabled: () => Boolean(selectedSpace.value),
})

function requestPayload() {
  return {
    space_id: form.space_id,
    title: form.title,
    category: form.category,
    language: form.language,
    framework: form.framework,
    task: form.task,
    rule: form.rule,
    prohibited_pattern: form.prohibited_pattern,
    examples: form.examples_text.split('\n').map((item) => item.trim()).filter(Boolean),
    citations: form.citations,
    status: form.status,
  }
}

const saveConvention = useMutation({
  mutationFn: async () => {
    const payload = requestPayload()
    if (editingId.value) {
      const changes: Partial<typeof payload> = { ...payload }
      delete changes.space_id
      return (await api.patch<CompanyConvention>(
        `/company-conventions/${editingId.value}`,
        changes,
        { headers: csrfHeaders() },
      )).data
    }
    return (await api.post<CompanyConvention>(
      '/company-conventions',
      payload,
      { headers: csrfHeaders() },
    )).data
  },
  onSuccess: async () => {
    showEditor.value = false
    await queryClient.invalidateQueries({ queryKey: ['company-conventions'] })
  },
})

function resetForm(spaceId = selectedSpace.value) {
  editingId.value = null
  Object.assign(form, {
    space_id: spaceId,
    title: '',
    category: 'architecture',
    language: '',
    framework: '',
    task: '',
    rule: '',
    prohibited_pattern: '',
    examples_text: '',
    status: 'draft',
    citations: [emptyCitation()],
  })
}

function openCreate() {
  saveConvention.reset()
  resetForm()
  showEditor.value = true
}

function openEdit(convention: CompanyConvention) {
  saveConvention.reset()
  editingId.value = convention.id
  Object.assign(form, {
    space_id: convention.space_id,
    title: convention.title,
    category: convention.category,
    language: convention.language,
    framework: convention.framework,
    task: convention.task,
    rule: convention.rule,
    prohibited_pattern: convention.prohibited_pattern,
    examples_text: convention.examples.join('\n'),
    status: convention.status,
    citations: convention.citations.map((citation) => ({ ...citation })),
  })
  showEditor.value = true
}

function closeEditor() {
  showEditor.value = false
}

function addCitation() {
  form.citations.push(emptyCitation())
}

function removeCitation(index: number) {
  if (form.citations.length > 1) form.citations.splice(index, 1)
}

function setCitationRepository(citation: ConventionCitation, repositoryId: string) {
  citation.repository_id = repositoryId
  citation.commit = repositoryById.value.get(repositoryId)?.last_commit ?? ''
}

function resetFilters() {
  Object.assign(filters, { language: '', framework: '', task: '' })
}
</script>

<template>
  <div class="page-container conventions-page">
    <section class="page-heading">
      <div><p class="eyebrow">ENGINEERING CONVENTIONS</p><h1>公司工程规范</h1></div>
      <button v-if="isAdmin" class="command-button" type="button" @click="openCreate">
        <Plus :size="17" />新增规范
      </button>
    </section>

    <section class="convention-toolbar" aria-label="规范筛选">
      <label><span>知识空间</span><select v-model="selectedSpace"><option v-for="space in spaces.data.value" :key="space.id" :value="space.id">{{ space.name }}</option></select></label>
      <label><span>语言</span><input v-model.trim="filters.language" placeholder="typescript" /></label>
      <label><span>框架</span><input v-model.trim="filters.framework" placeholder="vue" /></label>
      <label><span>任务</span><input v-model.trim="filters.task" placeholder="表单、API、测试" /></label>
      <button class="icon-button tooltip" type="button" data-tooltip="清空筛选" aria-label="清空筛选" @click="resetFilters"><RotateCcw :size="17" /></button>
    </section>

    <div v-if="conventions.error.value" class="error-banner">
      {{ errorMessage(conventions.error.value) }}
    </div>

    <section class="data-section">
      <div class="section-heading">
        <h2>规范条目</h2>
        <span>{{ conventions.data.value?.length ?? 0 }} 条</span>
      </div>
      <div v-if="conventions.data.value?.length" class="convention-list">
        <article v-for="convention in conventions.data.value" :key="convention.id" class="convention-row">
          <span class="convention-icon"><ScrollText :size="19" /></span>
          <div class="convention-main">
            <div class="convention-title-line">
              <strong>{{ convention.title }}</strong>
              <StatusBadge :status="convention.status" />
            </div>
            <div class="convention-tags">
              <span>{{ convention.category }}</span>
              <span v-if="convention.language">{{ convention.language }}</span>
              <span v-if="convention.framework">{{ convention.framework }}</span>
              <span v-if="convention.task">{{ convention.task }}</span>
            </div>
            <p>{{ convention.rule }}</p>
            <p v-if="convention.prohibited_pattern" class="prohibited-pattern"><strong>禁止：</strong>{{ convention.prohibited_pattern }}</p>
            <div class="citation-list">
              <span v-for="citation in convention.citations" :key="`${citation.repository_id}:${citation.path}:${citation.start_line}`">
                <FileCode2 :size="13" />
                {{ repositoryById.get(citation.repository_id)?.name ?? citation.repository_id }}
                <code>{{ shortCommit(citation.commit) }} · {{ citation.path }}:{{ citation.start_line }}-{{ citation.end_line }}</code>
              </span>
            </div>
          </div>
          <div class="convention-meta">
            <span>{{ formatDate(convention.updated_at) }}</span>
            <button v-if="isAdmin" class="icon-button tooltip" type="button" data-tooltip="编辑规范" aria-label="编辑规范" @click="openEdit(convention)"><Pencil :size="16" /></button>
          </div>
        </article>
      </div>
      <EmptyState v-else title="暂无匹配规范" />
    </section>

    <div v-if="showEditor" class="preview-backdrop" role="presentation" @click.self="closeEditor">
      <section v-modal-dialog="closeEditor" class="form-dialog convention-dialog" role="dialog" aria-modal="true" :aria-label="editingId ? '编辑规范' : '新增规范'">
        <header class="dialog-header">
          <div class="dialog-title"><ScrollText :size="20" /><h2>{{ editingId ? '编辑规范' : '新增规范' }}</h2></div>
          <button class="icon-button" type="button" aria-label="关闭" @click="closeEditor"><X :size="18" /></button>
        </header>
        <form class="stack-form two-column-form" @submit.prevent="saveConvention.mutate()">
          <label><span>知识空间</span><select v-model="form.space_id" :disabled="Boolean(editingId)" required><option v-for="space in spaces.data.value" :key="space.id" :value="space.id">{{ space.name }}</option></select></label>
          <label><span>状态</span><select v-model="form.status"><option value="draft">draft</option><option value="inferred">inferred</option><option value="confirmed">confirmed</option><option value="deprecated">deprecated</option></select></label>
          <label class="full-span"><span>标题</span><input v-model.trim="form.title" required /></label>
          <label><span>类别</span><input v-model.trim="form.category" required /></label>
          <label><span>适用任务</span><input v-model.trim="form.task" /></label>
          <label><span>语言</span><input v-model.trim="form.language" /></label>
          <label><span>框架</span><input v-model.trim="form.framework" /></label>
          <label class="full-span"><span>规则</span><textarea v-model.trim="form.rule" rows="4" required /></label>
          <label class="full-span"><span>禁止模式</span><textarea v-model.trim="form.prohibited_pattern" rows="2" /></label>
          <label class="full-span"><span>示例摘要（每行一条）</span><textarea v-model="form.examples_text" rows="3" /></label>

          <fieldset class="full-span citation-editor">
            <legend>源码引用</legend>
            <div v-for="(citation, index) in form.citations" :key="index" class="citation-editor-row">
              <label><span>仓库</span><select :value="citation.repository_id" required @change="setCitationRepository(citation, ($event.target as HTMLSelectElement).value)"><option value="">选择仓库</option><option v-for="repository in repositoriesInSpace" :key="repository.id" :value="repository.id">{{ repository.name }}</option></select></label>
              <label><span>Commit</span><input v-model.trim="citation.commit" required /></label>
              <label class="citation-path"><span>路径</span><input v-model.trim="citation.path" required /></label>
              <label><span>符号</span><input v-model.trim="citation.symbol" /></label>
              <label><span>起始行</span><input v-model.number="citation.start_line" type="number" min="1" required /></label>
              <label><span>结束行</span><input v-model.number="citation.end_line" type="number" :min="citation.start_line" required /></label>
              <button class="icon-button danger tooltip" type="button" data-tooltip="删除引用" aria-label="删除引用" :disabled="form.citations.length === 1" @click="removeCitation(index)"><Trash2 :size="16" /></button>
            </div>
            <button class="secondary-button citation-add" type="button" @click="addCitation"><Plus :size="15" />添加引用</button>
          </fieldset>

          <div v-if="saveConvention.error.value" class="error-banner full-span">{{ errorMessage(saveConvention.error.value) }}</div>
          <div class="form-actions full-span">
            <button class="secondary-button" type="button" @click="closeEditor">取消</button>
            <button class="command-button" type="submit" :disabled="saveConvention.isPending.value">{{ saveConvention.isPending.value ? '保存中…' : '保存规范' }}</button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
