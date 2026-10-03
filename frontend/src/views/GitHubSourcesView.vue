<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Check, Copy, Github, KeyRound, Plus, RefreshCw, X } from 'lucide-vue-next'
import { reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate } from '@/format'
import {
  copyText,
  GITHUB_HTTPS_CLONE_PATTERN,
  GITHUB_SSH_CLONE_PATTERN,
  normalizeGitHubCloneUrl,
  normalizeOpenSshPublicKey,
} from '@/sshKey'
import type { GitHubSource } from '@/types'

const queryClient = useQueryClient()
const showCreate = ref(false)
const formError = ref('')
const generatedKey = ref<{ key_id: string; public_key: string } | null>(null)
const copied = ref(false)
const keyInput = ref<HTMLInputElement | null>(null)
const form = reactive<{
  name: string
  repo_url: string
  branch: string
  poll_interval_seconds: number
  visibility: 'public' | 'private'
  description: string
  include_paths_text: string
  pinned_commit: string
  license_name: string
  license_url: string
}>({
  name: '',
  repo_url: 'git@github.com:owner/repository.git',
  branch: 'main',
  poll_interval_seconds: 1800,
  visibility: 'private',
  description: '',
  include_paths_text: '',
  pinned_commit: '',
  license_name: '',
  license_url: '',
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
      name: form.name,
      repo_url: form.repo_url,
      branch: form.branch,
      poll_interval_seconds: form.poll_interval_seconds,
      visibility: form.visibility,
      description: form.description,
      include_paths: form.include_paths_text.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
      pinned_commit: form.pinned_commit.trim(),
      license_name: form.license_name.trim(),
      license_url: form.license_url.trim(),
      ssh_key_id: form.visibility === 'private' ? generatedKey.value?.key_id : undefined,
    }, { headers: csrfHeaders() })
  ).data,
  onSuccess: async () => {
    showCreate.value = false
    generatedKey.value = null
    Object.assign(form, {
      name: '', repo_url: 'git@github.com:owner/repository.git', branch: 'main',
      poll_interval_seconds: 1800, visibility: 'private', description: '',
      include_paths_text: '',
      pinned_commit: '', license_name: '', license_url: '',
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
  form.repo_url = normalizeGitHubCloneUrl(form.repo_url, form.visibility)
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
        <span>CodeAtlas 按各来源配置的间隔检查，发现新提交后自动建立索引任务</span>
      </div>
      <div v-if="sources.data.value?.length" class="source-card-grid">
        <article v-for="source in sources.data.value" :key="source.id" class="source-card">
          <span class="source-card-icon"><Github :size="20" /></span>
          <span class="source-card-main">
            <strong>{{ source.name }}</strong>
            <small>{{ source.repo_url }}</small>
            <small>分支 {{ source.branch }} · {{ source.repository_status }}</small>
            <small>索引范围 {{ source.include_paths.length ? source.include_paths.join(' · ') : '整个仓库' }}</small>
            <small v-if="source.pinned_commit">固定提交 {{ source.pinned_commit }}</small>
            <small v-if="source.license_name">许可证 {{ source.license_name }}</small>
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
      <section v-modal-dialog="closeDialog" class="form-dialog" role="dialog" aria-modal="true" aria-label="添加 GitHub 仓库">
        <header class="dialog-header">
          <div><p class="eyebrow">NEW GITHUB SOURCE</p><h2>添加 GitHub 仓库</h2></div>
          <button class="icon-button" type="button" aria-label="关闭" @click="closeDialog"><X :size="18" /></button>
        </header>
        <div v-if="form.visibility === 'public'" class="key-setup-block">
          <div class="key-setup-icon"><Github :size="20" /></div>
          <div><strong>公开仓库无需 Deploy Key</strong><p>使用 GitHub Code → HTTPS 地址即可读取公开源码。</p></div>
        </div>
        <div v-else-if="!generatedKey" class="key-setup-block">
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
          <label class="full-span">
            <span>{{ form.visibility === 'public' ? 'HTTPS Clone URL' : 'SSH Clone URL' }}</span>
            <input
              v-model="form.repo_url"
              :pattern="form.visibility === 'public' ? GITHUB_HTTPS_CLONE_PATTERN : GITHUB_SSH_CLONE_PATTERN"
              required
              :placeholder="form.visibility === 'public' ? 'https://github.com/owner/repository.git' : 'git@github.com:owner/repository.git'"
              @blur="normalizeCloneUrl"
            />
            <small class="form-hint">{{ form.visibility === 'public' ? '公开仓库使用 GitHub Code → HTTPS 地址，无需添加 Deploy Key。' : '私有仓库使用 GitHub Code → SSH 地址，并将上方公钥添加为只读 Deploy Key。' }}</small>
          </label>
          <label><span>检查间隔（秒）</span><input v-model.number="form.poll_interval_seconds" type="number" min="300" max="86400" required /></label>
          <label><span>可见性</span><select v-model="form.visibility" @change="normalizeCloneUrl"><option value="private">private</option><option value="public">public</option></select></label>
          <label class="full-span"><span>描述</span><textarea v-model="form.description" rows="2" /></label>
          <label class="full-span">
            <span>索引目录（可选，每行一个）</span>
            <textarea v-model="form.include_paths_text" rows="4" placeholder="templates/website/src/Header&#10;templates/website/src/blocks&#10;templates/website/src/fields" />
            <small class="form-hint">留空索引整个仓库；只索引选定目录可减少无关代码和 embedding 消耗。路径相对于仓库根目录，不支持通配符。</small>
          </label>
          <label class="full-span" v-if="form.visibility === 'public'">
            <span>固定 Git 提交 SHA（可选）</span>
            <input v-model="form.pinned_commit" maxlength="40" pattern="[0-9a-fA-F]{40}" placeholder="40 位 commit SHA" />
          </label>
          <label><span>许可证名称</span><input v-model="form.license_name" maxlength="100" placeholder="MIT" /></label>
          <label><span>许可证链接</span><input v-model="form.license_url" maxlength="1000" type="url" placeholder="https://github.com/owner/repo/blob/main/LICENSE" /></label>
          <p class="form-hint full-span">{{ form.visibility === 'public' ? '公开仓库通过 HTTPS 只读同步，不需要密钥。' : '每个私有仓库使用独立 Deploy Key。私钥不会返回页面，也不会写入数据库。' }}</p>
          <div v-if="formError" class="error-banner full-span">{{ formError }}</div>
          <div class="form-actions full-span">
            <button class="secondary-button" type="button" @click="closeDialog">取消</button>
            <button class="command-button" type="submit" :disabled="(form.visibility === 'private' && !generatedKey) || createSource.isPending.value">保存并启用自动同步</button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
