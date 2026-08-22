<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Check, Copy, KeyRound, Plus, Trash2, X } from 'lucide-vue-next'
import { reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate } from '@/format'
import type { ApiToken, Repository } from '@/types'

const queryClient = useQueryClient()
const showCreate = ref(false)
const revealedToken = ref('')
const copied = ref(false)
const form = reactive({ name: '', scopes: ['status', 'search', 'read'], repository_ids: [] as string[] })
const expiresInDays = ref<number | null>(null)

const tokens = useQuery({
  queryKey: ['tokens'],
  queryFn: async () => (await api.get<ApiToken[]>('/tokens')).data,
})
const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})

const createToken = useMutation({
  mutationFn: async () =>
    (await api.post<ApiToken>('/tokens', {
      ...form,
      expires_in_days: expiresInDays.value,
    }, { headers: csrfHeaders() })).data,
  onSuccess: async (token) => {
    revealedToken.value = token.token ?? ''
    showCreate.value = false
    Object.assign(form, { name: '', scopes: ['status', 'search', 'read'], repository_ids: [] })
    expiresInDays.value = null
    await queryClient.invalidateQueries({ queryKey: ['tokens'] })
  },
})
const revokeToken = useMutation({
  mutationFn: async (id: string) => api.delete(`/tokens/${id}`, { headers: csrfHeaders() }),
  onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['tokens'] }),
})

const confirmRevoke = ref<string | null>(null)

async function copyToken() {
  await navigator.clipboard.writeText(revealedToken.value)
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1500)
}

function toggleScope(scope: string, checked: boolean) {
  form.scopes = checked
    ? [...new Set([...form.scopes, scope])]
    : form.scopes.filter((item) => item !== scope)
}

function toggleRepository(id: string, checked: boolean) {
  form.repository_ids = checked
    ? [...new Set([...form.repository_ids, id])]
    : form.repository_ids.filter((item) => item !== id)
}
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">MCP ACCESS</p><h1>API Token</h1></div>
      <button class="command-button" type="button" @click="showCreate = true"><Plus :size="17" />创建 Token</button>
    </section>

    <section class="data-section">
      <div v-if="tokens.data.value?.length" class="token-list">
        <article v-for="token in tokens.data.value" :key="token.id" class="token-row">
          <span class="token-icon"><KeyRound :size="18" /></span>
          <div><strong>{{ token.name }}</strong><code>{{ token.prefix }}••••••••</code></div>
          <div class="scope-list"><span v-for="scope in token.scopes" :key="scope">{{ scope }}</span></div>
          <span>{{ token.repository_ids.length ? `${token.repository_ids.length} repos` : 'public repos' }}</span>
          <span>{{ token.revoked_at ? 'revoked' : (token.expires_at ? formatDate(token.expires_at) : formatDate(token.created_at)) }}</span>
          <button v-if="!token.revoked_at" class="icon-button danger tooltip" type="button" data-tooltip="撤销 Token" aria-label="撤销 Token" @click="confirmRevoke = token.id"><Trash2 :size="17" /></button>
        </article>
      </div>
      <EmptyState v-else title="暂无 Token" />
    </section>

    <div v-if="confirmRevoke" class="preview-backdrop" role="presentation" @click.self="confirmRevoke = null">
      <section class="form-dialog compact-dialog" role="dialog" aria-modal="true" aria-label="确认撤销">
        <header class="dialog-header"><div class="dialog-title"><Trash2 :size="20" /><h2>确认撤销</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="confirmRevoke = null"><X :size="18" /></button></header>
        <p>确定要撤销此 Token 吗？撤销后无法恢复。</p>
        <div class="dialog-actions">
          <button class="command-button" type="button" @click="confirmRevoke = null">取消</button>
          <button class="command-button danger" type="button" @click="revokeToken.mutate(confirmRevoke); confirmRevoke = null">确认撤销</button>
        </div>
      </section>
    </div>

    <div v-if="showCreate" class="preview-backdrop" role="presentation" @click.self="showCreate = false">
      <section class="form-dialog compact-dialog" role="dialog" aria-modal="true" aria-label="创建 Token">
        <header class="dialog-header"><div class="dialog-title"><KeyRound :size="20" /><h2>创建 Token</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="showCreate = false"><X :size="18" /></button></header>
        <form class="stack-form" @submit.prevent="createToken.mutate()">
          <label><span>名称</span><input v-model="form.name" required /></label>
          <label><span>过期天数（可选）</span><input v-model.number="expiresInDays" type="number" min="1" max="365" placeholder="永不过期" /></label>
          <fieldset><legend>权限范围</legend><label v-for="scope in ['status', 'search', 'read']" :key="scope" class="check-row"><input type="checkbox" :checked="form.scopes.includes(scope)" @change="toggleScope(scope, ($event.target as HTMLInputElement).checked)" /><span>{{ scope }}</span></label></fieldset>
          <fieldset><legend>仓库范围</legend><label v-for="repo in repositories.data.value" :key="repo.id" class="check-row"><input type="checkbox" :checked="form.repository_ids.includes(repo.id)" @change="toggleRepository(repo.id, ($event.target as HTMLInputElement).checked)" /><span>{{ repo.name }}</span></label><small>未选择时仅允许公开仓库</small></fieldset>
          <div v-if="createToken.error.value" class="error-banner">{{ errorMessage(createToken.error.value) }}</div>
          <button class="command-button full-width" type="submit" :disabled="!form.name || !form.scopes.length || createToken.isPending.value">创建 Token</button>
        </form>
      </section>
    </div>

    <div v-if="revealedToken" class="preview-backdrop" role="presentation">
      <section class="secret-dialog" role="dialog" aria-modal="true" aria-label="新 Token">
        <span class="success-icon"><Check :size="22" /></span>
        <h2>Token 已创建</h2>
        <div class="secret-value"><code>{{ revealedToken }}</code><button class="icon-button tooltip" type="button" data-tooltip="复制" aria-label="复制" @click="copyToken"><Check v-if="copied" :size="17" /><Copy v-else :size="17" /></button></div>
        <button class="command-button full-width" type="button" @click="revealedToken = ''">完成</button>
      </section>
    </div>
  </div>
</template>
