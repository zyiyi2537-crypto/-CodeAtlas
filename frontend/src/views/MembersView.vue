<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Plus, ShieldCheck, Trash2, UserPlus, X } from 'lucide-vue-next'
import { reactive, ref } from 'vue'

import { api, errorMessage } from '@/api'
import { csrfHeaders } from '@/auth'
import EmptyState from '@/components/EmptyState.vue'
import { formatDate } from '@/format'
import type { Repository, User } from '@/types'

const queryClient = useQueryClient()
const showCreate = ref(false)
const formError = ref('')
const grant = reactive({ userId: '', repositoryId: '' })
const form = reactive({
  email: '',
  display_name: '',
  password: '',
  role: 'member',
})

const members = useQuery({
  queryKey: ['members'],
  queryFn: async () => (await api.get<User[]>('/members')).data,
})
const repositories = useQuery({
  queryKey: ['repositories'],
  queryFn: async () => (await api.get<Repository[]>('/repositories')).data,
})

const createMember = useMutation({
  mutationFn: async () =>
    (await api.post<User>('/members', form, { headers: csrfHeaders() })).data,
  onSuccess: async () => {
    showCreate.value = false
    Object.assign(form, { email: '', display_name: '', password: '', role: 'member' })
    await queryClient.invalidateQueries({ queryKey: ['members'] })
  },
  onError: (error) => { formError.value = errorMessage(error) },
})

const grantRepository = useMutation({
  mutationFn: async () =>
    api.put(
      `/members/${grant.userId}/repositories/${grant.repositoryId}`,
      null,
      { headers: csrfHeaders() },
    ),
})

const updateMember = useMutation({
  mutationFn: async ({ userId, role, isActive }: { userId: string; role?: string; isActive?: boolean }) =>
    api.patch(`/members/${userId}`, { role, is_active: isActive }, { headers: csrfHeaders() }),
  onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: ['members'] })
  },
})

const deleteMember = useMutation({
  mutationFn: async (userId: string) =>
    api.delete(`/members/${userId}`, { headers: csrfHeaders() }),
  onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: ['members'] })
  },
})

function removeMember(member: User) {
  const confirmed = window.confirm(
    `永久删除成员“${member.display_name}”？\n\n` +
    '该账号的会话、消息、长期记忆、登录Session、仓库授权和个人Token都会清除；' +
    '其创建的共享知识资产会转交给当前管理员。此操作不可撤销。',
  )
  if (confirmed) deleteMember.mutate(member.id)
}

function closeCreateDialog() {
  showCreate.value = false
}
</script>

<template>
  <div class="page-container">
    <section class="page-heading">
      <div><p class="eyebrow">ACCESS CONTROL</p><h1>成员</h1></div>
      <button class="command-button" type="button" @click="showCreate = true">
        <Plus :size="17" />新增成员
      </button>
    </section>

    <section class="grant-band">
      <ShieldCheck :size="20" />
      <label><span>成员</span><select v-model="grant.userId"><option value="">选择成员</option><option v-for="member in members.data.value?.filter((item) => item.role === 'member')" :key="member.id" :value="member.id">{{ member.display_name }}</option></select></label>
      <label><span>仓库</span><select v-model="grant.repositoryId"><option value="">选择仓库</option><option v-for="repo in repositories.data.value" :key="repo.id" :value="repo.id">{{ repo.name }}</option></select></label>
      <button class="command-button" type="button" :disabled="!grant.userId || !grant.repositoryId || grantRepository.isPending.value" @click="grantRepository.mutate()">授权</button>
      <span v-if="grantRepository.isSuccess.value" class="success-text">已授权</span>
      <span v-if="grantRepository.error.value" class="error-text">{{ errorMessage(grantRepository.error.value) }}</span>
    </section>

    <section class="data-section">
      <div v-if="members.data.value?.length" class="member-list">
        <article v-for="member in members.data.value" :key="member.id" class="member-row">
          <div class="avatar">{{ member.display_name.slice(0, 1).toUpperCase() }}</div>
          <div><strong>{{ member.display_name }}</strong><span>{{ member.email }}</span></div>
          <span class="role-badge">{{ member.role }}</span>
          <span>{{ member.is_active ? 'active' : 'disabled' }}</span>
          <span>{{ formatDate(member.created_at) }}</span>
          <div class="row-actions">
            <button
              class="icon-button tooltip"
              type="button"
              :data-tooltip="member.is_active ? '禁用成员' : '启用成员'"
              :aria-label="member.is_active ? '禁用成员' : '启用成员'"
              @click="updateMember.mutate({ userId: member.id, isActive: !member.is_active })"
            >
              <ShieldCheck :size="17" />
            </button>
            <button
              class="icon-button danger tooltip"
              type="button"
              data-tooltip="删除成员"
              aria-label="删除成员"
              @click="removeMember(member)"
            >
              <Trash2 :size="17" />
            </button>
          </div>
        </article>
      </div>
      <EmptyState v-else title="暂无成员" />
    </section>

    <div v-if="showCreate" class="preview-backdrop" role="presentation" @click.self="closeCreateDialog">
      <section v-modal-dialog="closeCreateDialog" class="form-dialog compact-dialog" role="dialog" aria-modal="true" aria-label="新增成员">
        <header class="dialog-header">
          <div class="dialog-title"><UserPlus :size="20" /><h2>新增成员</h2></div>
          <button class="icon-button" type="button" aria-label="关闭" @click="closeCreateDialog"><X :size="18" /></button>
        </header>
        <form class="stack-form" @submit.prevent="createMember.mutate()">
          <label><span>显示名称</span><input v-model="form.display_name" required /></label>
          <label><span>邮箱</span><input v-model="form.email" type="email" required /></label>
          <label><span>初始密码</span><input v-model="form.password" type="password" minlength="12" required /></label>
          <label><span>角色</span><select v-model="form.role"><option value="member">member</option><option value="admin">admin</option></select></label>
          <div v-if="formError" class="error-banner">{{ formError }}</div>
          <button class="command-button full-width" type="submit" :disabled="createMember.isPending.value">创建成员</button>
        </form>
      </section>
    </div>
  </div>
</template>
