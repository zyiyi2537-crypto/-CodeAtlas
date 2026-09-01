<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Check, Copy, KeyRound, Plus, Server, Trash2, X } from 'lucide-vue-next'
import { computed, reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate } from '@/format'
import type { ApiToken, Repository } from '@/types'

const queryClient = useQueryClient()
const showCreate = ref(false)
const activeTokenId = ref<string | null>(null)
const sessionTokens = reactive<Record<string, string>>({})
const copied = ref<'token' | 'config' | ''>('')
const installTarget = ref<'codex' | 'claude' | 'json'>('codex')
const form = reactive({ name: '', scopes: ['status', 'search', 'read'], repository_ids: [] as string[] })
const expiresInDays = ref<number | null>(null)

const mcpUrl = computed(() => `${window.location.origin}/mcp`)
const revealedToken = computed(() => activeTokenId.value ? (sessionTokens[activeTokenId.value] ?? '') : '')
const isSecureMcp = computed(() => window.location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(window.location.hostname))
const installConfig = computed(() => {
  const authorization = `Bearer ${revealedToken.value}`
  if (installTarget.value === 'codex') {
    return `[mcp_servers.codeatlas]\nurl = "${mcpUrl.value}"\nhttp_headers = { Authorization = "${authorization}" }`
  }
  if (installTarget.value === 'claude') {
    return `claude mcp add --transport http --scope user codeatlas "${mcpUrl.value}" --header "Authorization: ${authorization}"`
  }
  return JSON.stringify({
    mcpServers: {
      codeatlas: {
        type: 'http',
        url: mcpUrl.value,
        headers: { Authorization: authorization },
      },
    },
  }, null, 2)
})

const tokens = useQuery({
  queryKey: ['tokens'],
  queryFn: async () => (await api.get<ApiToken[]>('/tokens')).data,
})
const activeTokens = computed(() => tokens.data.value?.filter((token) => !token.revoked_at) ?? [])
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
    if (token.token) sessionTokens[token.id] = token.token
    activeTokenId.value = token.id
    showCreate.value = false
    Object.assign(form, { name: '', scopes: ['status', 'search', 'read'], repository_ids: [] })
    expiresInDays.value = null
    await queryClient.invalidateQueries({ queryKey: ['tokens'] })
  },
})
const revokeToken = useMutation({
  mutationFn: async (id: string) => api.delete(`/tokens/${id}`, { headers: csrfHeaders() }),
  onSuccess: async (_, id) => {
    delete sessionTokens[id]
    queryClient.setQueryData<ApiToken[]>(['tokens'], (current) => current?.filter((token) => token.id !== id) ?? [])
    confirmRevoke.value = null
    await queryClient.invalidateQueries({ queryKey: ['tokens'] })
  },
})

const confirmRevoke = ref<string | null>(null)

function beginRevoke(id: string) {
  revokeToken.reset()
  confirmRevoke.value = id
}

function closeRevokeDialog() {
  confirmRevoke.value = null
}

function closeCreateDialog() {
  showCreate.value = false
}

function openConnectionDialog(id: string) {
  if (sessionTokens[id]) activeTokenId.value = id
}

async function copyValue(kind: 'token' | 'config', value: string) {
  await navigator.clipboard.writeText(value)
  copied.value = kind
  window.setTimeout(() => {
    if (copied.value === kind) copied.value = ''
  }, 1500)
}

function closeConnectionDialog() {
  activeTokenId.value = null
  copied.value = ''
  installTarget.value = 'codex'
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
      <div v-if="activeTokens.length" class="token-list">
        <article v-for="token in activeTokens" :key="token.id" class="token-row">
          <span class="token-icon"><KeyRound :size="18" /></span>
          <div><strong>{{ token.name }}</strong><code>{{ token.prefix }}••••••••</code></div>
          <div class="scope-list"><span v-for="scope in token.scopes" :key="scope">{{ scope }}</span></div>
          <span>{{ token.repository_ids.length ? `${token.repository_ids.length} repos` : 'public repos' }}</span>
          <span>{{ token.expires_at ? formatDate(token.expires_at) : formatDate(token.created_at) }}</span>
          <div class="token-actions">
            <button v-if="sessionTokens[token.id]" class="icon-button tooltip" type="button" data-tooltip="查看连接配置" aria-label="查看连接配置" @click="openConnectionDialog(token.id)"><Server :size="17" /></button>
            <button class="icon-button danger tooltip" type="button" data-tooltip="撤销 Token" aria-label="撤销 Token" @click="beginRevoke(token.id)"><Trash2 :size="17" /></button>
          </div>
        </article>
      </div>
      <EmptyState v-else title="暂无 Token" />
    </section>

    <div v-if="confirmRevoke" class="preview-backdrop" role="presentation" @click.self="closeRevokeDialog">
      <section v-modal-dialog="closeRevokeDialog" class="form-dialog compact-dialog" role="dialog" aria-modal="true" aria-label="确认撤销">
        <header class="dialog-header"><div class="dialog-title"><Trash2 :size="20" /><h2>确认撤销</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="closeRevokeDialog"><X :size="18" /></button></header>
        <p>确定要撤销此 Token 吗？撤销成功后将从有效 Token 列表移除，且无法恢复。</p>
        <div v-if="revokeToken.error.value" class="error-banner">{{ errorMessage(revokeToken.error.value) }}</div>
        <div class="dialog-actions">
          <button class="secondary-button" type="button" :disabled="revokeToken.isPending.value" @click="closeRevokeDialog">取消</button>
          <button class="command-button danger" type="button" :disabled="revokeToken.isPending.value" @click="revokeToken.mutate(confirmRevoke!)">{{ revokeToken.isPending.value ? '正在撤销…' : '确认撤销' }}</button>
        </div>
      </section>
    </div>

    <div v-if="showCreate" class="preview-backdrop" role="presentation" @click.self="closeCreateDialog">
      <section v-modal-dialog="closeCreateDialog" class="form-dialog compact-dialog" role="dialog" aria-modal="true" aria-label="创建 Token">
        <header class="dialog-header"><div class="dialog-title"><KeyRound :size="20" /><h2>创建 Token</h2></div><button class="icon-button" type="button" aria-label="关闭" @click="closeCreateDialog"><X :size="18" /></button></header>
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
      <section v-modal-dialog="closeConnectionDialog" class="secret-dialog mcp-connect-dialog" role="dialog" aria-modal="true" aria-label="连接 MCP 客户端">
        <header class="mcp-connect-header">
          <span class="success-icon"><Server :size="22" /></span>
          <div><p class="eyebrow">REMOTE MCP / STREAMABLE HTTP</p><h2>连接 MCP 客户端</h2></div>
        </header>

        <div class="mcp-connection-fields">
          <div class="connection-field">
            <span>服务地址</span>
            <code>{{ mcpUrl }}</code>
          </div>
          <div class="connection-field">
            <span>访问 Token · 仅显示一次</span>
            <div><code>{{ revealedToken }}</code><button class="icon-button tooltip" type="button" data-tooltip="复制 Token" aria-label="复制 Token" @click="copyValue('token', revealedToken)"><Check v-if="copied === 'token'" :size="17" /><Copy v-else :size="17" /></button></div>
          </div>
        </div>

        <div class="mcp-client-config">
          <div class="config-heading">
            <div class="segmented-control" aria-label="客户端配置">
              <button type="button" :class="{ active: installTarget === 'codex' }" @click="installTarget = 'codex'">Codex</button>
              <button type="button" :class="{ active: installTarget === 'claude' }" @click="installTarget = 'claude'">Claude Code</button>
              <button type="button" :class="{ active: installTarget === 'json' }" @click="installTarget = 'json'">通用 JSON</button>
            </div>
            <button class="secondary-button compact-copy" type="button" @click="copyValue('config', installConfig)"><Check v-if="copied === 'config'" :size="16" /><Copy v-else :size="16" />{{ copied === 'config' ? '已复制' : '复制配置' }}</button>
          </div>
          <pre><code>{{ installConfig }}</code></pre>
          <p v-if="installTarget === 'codex'" class="config-note">添加到 <code>~/.codex/config.toml</code>，然后重新启动 Codex。</p>
          <p v-else-if="installTarget === 'claude'" class="config-note">在终端执行该命令，然后使用 <code>claude mcp list</code> 检查连接。</p>
          <p v-else class="config-note">适用于支持远程 HTTP MCP 和自定义请求头的客户端。</p>
        </div>

        <div v-if="!isSecureMcp" class="security-note critical"><KeyRound :size="16" /><span>当前服务使用 HTTP，Bearer Token 会以明文经过网络。公网生产环境必须先启用 HTTPS，再连接 Codex 或 Claude。</span></div>
        <div v-else class="security-note"><KeyRound :size="16" /><span>配置包含访问凭据。当前页面会话内可从 Token 列表再次查看；刷新或离开页面后无法恢复。</span></div>
        <button class="command-button full-width" type="button" @click="closeConnectionDialog">我已保存配置</button>
      </section>
    </div>
  </div>
</template>
