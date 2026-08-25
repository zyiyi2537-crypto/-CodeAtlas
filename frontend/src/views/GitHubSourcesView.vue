<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Check, Copy, Github, KeyRound, Plus, RefreshCw, X } from 'lucide-vue-next'
import { reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate } from '@/format'
import { copyText, normalizeGitHubCloneUrl, normalizeOpenSshPublicKey } from '@/sshKey'
import type { GitHubSource } from '@/types'

const queryClient = useQueryClient()
const showCreate = ref(false)
const formError = ref('')
const generatedKey = ref<{ key_id: string; public_key: string } | null>(null)
const copied = ref(false)
const keyInput = ref<HTMLInputElement | null>(null)
const form = reactive({
  name: '',
  repo_url: 'git@github.com:owner/repository.git',
  branch: 'main',
  poll_interval_seconds: 1800,
  visibility: 'private',
  description: '',
})

const sources = useQuery({
  queryKey: ['github-sources'],
  queryFn: async () => (await api.get<GitHubSource[]>('/github-sources')).data,
})

const generateKey = useMutation({
  mutationFn: async () => (await api.post('/github-keys', null, { headers: csrfHeaders() })).data,
  onSuccess: (key) => {
    generatedKey.value = key
    formError.value = ''
  },
  onError: (error) => { formError.value = errorMessage(error) },
})

const createSource = useMutation({
  mutationFn: async () => (
    await api.post('/github-sources', {
      ...form,
      ssh_key_id: generatedKey.value?.key_id,
    }, { headers: csrfHeaders() })
  ).data,
  onSuccess: async () => {
    showCreate.value = false
    generatedKey.value = null
    Object.assign(form, {
      name: '', repo_url: 'git@github.com:owner/repository.git', branch: 'main',
      poll_interval_seconds: 1800, visibility: 'private', description: '',
    })
    await queryClient.invalidateQueries({ queryKey: ['github-sources'] })
    await queryClient.invalidateQueries({ queryKey: ['repositories'] })
  },
  onError: (error) => { formError.value = errorMessage(error) },
})

const checkSource = useMutation({
  mutationFn: async (id: string) => api.post(`/github-sources/${id}/check`, null, { headers: csrfHeaders() }),
  onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['github-sources'] }),
})

async function copyKey() {
  if (!generatedKey.value) return
  try {
    await copyText(normalizeOpenSshPublicKey(generatedKey.value.public_key), keyInput.value ?? undefined)
    copied.value = true
    formError.value = ''
    window.setTimeout(() => { copied.value = false }, 1400)
  } catch (error) {
    formError.value = errorMessage(error)
  }
}

function normalizeCloneUrl() {
  form.repo_url = normalizeGitHubCloneUrl(form.repo_url)
}

function closeDialog() {
  showCreate.value = false
  formError.value = ''
}
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">GITHUB SOURCES</p><h1>GitHub SSH 来源</h1></div>
      <button class="command-button" type="button" @click="showCreate = true">
        <Plus :size="17" />添加 GitHub 仓库
      </button>
    </section>

    <div v-if="sources.error.value" class="error-banner">{{ errorMessage(sources.error.value) }}</div>
    <section class="data-section">
      <div class="section-heading">
        <h2>自动同步仓库</h2>
        <span>CodeAtlas 每分钟检查一次，发现新提交后自动建立索引任务</span>
      </div>
      <div v-if="sources.data.value?.length" class="source-card-grid">
        <article v-for="source in sources.data.value" :key="source.id" class="source-card">
          <span class="source-card-icon"><Github :size="20" /></span>
          <span class="source-card-main">
            <strong>{{ source.name }}</strong>
            <small>{{ source.repo_url }}</small>
            <small>分支 {{ source.branch }} · {{ source.repository_status }}</small>
            <small v-if="source.last_error" class="error-text">{{ source.last_error }}</small>
          </span>
          <span class="source-card-meta">
            <span :class="source.last_error ? 'status-error' : 'status-ready'">
              {{ source.last_error ? '检查失败' : '已启用' }}
            </span>
            <small>检查于 {{ formatDate(source.last_checked_at) }}</small>
            <button class="icon-button tooltip" type="button" data-tooltip="立即检查" aria-label="立即检查" @click="checkSource.mutate(source.id)">
              <RefreshCw :size="16" />
            </button>
          </span>
        </article>
      </div>
      <EmptyState v-else title="尚未配置 GitHub 仓库" description="生成 Deploy Key 并添加到 GitHub 后即可自动同步。" />
    </section>

    <div v-if="showCreate" class="preview-backdrop" role="presentation" @click.self="closeDialog">
      <section class="form-dialog" role="dialog" aria-modal="true" aria-label="添加 GitHub 仓库">
        <header class="dialog-header">
          <div><p class="eyebrow">NEW GITHUB SOURCE</p><h2>添加 GitHub 仓库</h2></div>
          <button class="icon-button" type="button" aria-label="关闭" @click="closeDialog"><X :size="18" /></button>
        </header>
        <div v-if="!generatedKey" class="key-setup-block">
          <div class="key-setup-icon"><KeyRound :size="20" /></div>
          <div><strong>先生成只读 Deploy Key</strong><p>公钥会显示在下一步，私钥只保存在服务器上。</p></div>
          <button class="command-button" type="button" :disabled="generateKey.isPending.value" @click="generateKey.mutate()">生成 Key</button>
        </div>
        <div v-else class="key-display-block">
          <div class="section-heading"><div><h3>复制公钥到 GitHub</h3><span>Repository Settings → Deploy keys → Add deploy key → 勾选只读</span></div><Check v-if="copied" :size="18" /></div>
          <input ref="keyInput" class="ssh-public-key-field" :value="normalizeOpenSshPublicKey(generatedKey.public_key)" readonly spellcheck="false" @focus="($event.target as HTMLInputElement).select()" />
          <button class="secondary-button" type="button" @click="copyKey"><Copy :size="16" />{{ copied ? '已复制' : '复制公钥' }}</button>
          <p class="form-hint">只复制上面这一整行。若浏览器禁止自动复制，点击输入框后按 Ctrl+C。</p>
        </div>
        <form class="stack-form two-column-form" @submit.prevent="createSource.mutate()">
          <label><span>来源名称</span><input v-model="form.name" placeholder="my-github-repo" required /></label>
          <label><span>分支</span><input v-model="form.branch" required /></label>
          <label class="full-span"><span>SSH Clone URL</span><input v-model="form.repo_url" pattern="git@github\\.com:.+/.+\\.git" required placeholder="git@github.com:owner/repository.git" @blur="normalizeCloneUrl" /><small class="form-hint">必须是 GitHub Code → SSH 地址；粘贴 HTTPS 地址后离开输入框会自动转换。</small></label>
          <label><span>检查间隔（秒）</span><input v-model.number="form.poll_interval_seconds" type="number" min="300" max="86400" required /></label>
          <label><span>可见性</span><select v-model="form.visibility"><option value="private">private</option><option value="public">public</option></select></label>
          <label class="full-span"><span>描述</span><textarea v-model="form.description" rows="2" /></label>
          <p class="form-hint full-span">每个仓库使用独立 Deploy Key。私钥不会返回页面，也不会写入数据库。</p>
          <div v-if="formError" class="error-banner full-span">{{ formError }}</div>
          <div class="form-actions full-span">
            <button class="secondary-button" type="button" @click="closeDialog">取消</button>
            <button class="command-button" type="submit" :disabled="!generatedKey || createSource.isPending.value">保存并启用自动同步</button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
