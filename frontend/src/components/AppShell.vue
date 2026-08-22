<script setup lang="ts">
import {
  BookOpen,
  Boxes,
  Cloud,
  Cpu,
  Database,
  FileText,
  FileCode2,
  FolderTree,
  Gauge,
  KeyRound,
  LogIn,
  LogOut,
  Menu,
  MessageSquareText,
  Search,
  Users,
  X,
} from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { logout, useAuth } from '@/auth'
import { api } from '@/api'
import { csrfHeaders } from '@/auth'

const route = useRoute()
const router = useRouter()
const { state, isAdmin } = useAuth()
const menuOpen = ref(false)

const navigation = computed(() => {
  const items = [
    { to: '/', label: '代码搜索', icon: Search, public: true },
    { to: '/chat', label: '代码问答', icon: MessageSquareText },
    { to: '/browse', label: '代码浏览', icon: FolderTree },
    { to: '/overview', label: '概览', icon: Gauge },
    { to: '/repositories', label: '仓库', icon: Database, admin: true },
    { to: '/gitlab-sources', label: 'GitLab 来源', icon: Cloud, admin: true },
    { to: '/documents', label: '项目文档', icon: FileText },
    { to: '/embedding-profiles', label: 'Embedding 模型', icon: Cpu, admin: true },
    { to: '/jobs', label: '索引任务', icon: Boxes },
    { to: '/members', label: '成员', icon: Users, admin: true },
    { to: '/tokens', label: 'API Token', icon: KeyRound, admin: true },
  ]
  return items.filter((item) => item.public || (state.user && (!item.admin || isAdmin.value)))
})

async function signOut() {
  await logout()
  menuOpen.value = false
  await router.push('/')
}

async function signOutAll() {
  await api.post('/auth/logout-all', null, { headers: csrfHeaders() })
  state.user = null
  state.csrfToken = ''
  menuOpen.value = false
  await router.push('/')
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="/" aria-label="CodeAtlas 博客首页">
        <span class="brand-mark"><FileCode2 :size="20" /></span>
        <span>CodeAtlas</span>
      </a>
      <div class="topbar-actions">
        <a class="icon-button tooltip" href="/" data-tooltip="技术博客" aria-label="技术博客">
          <BookOpen :size="18" />
        </a>
        <RouterLink
          v-if="!state.user"
          class="command-button compact"
          to="/login"
        >
          <LogIn :size="16" />
          控制台登录
        </RouterLink>
        <button
          class="icon-button mobile-menu-button"
          type="button"
          :aria-label="menuOpen ? '关闭菜单' : '打开菜单'"
          @click="menuOpen = !menuOpen"
        >
          <X v-if="menuOpen" :size="20" />
          <Menu v-else :size="20" />
        </button>
      </div>
    </header>

    <aside class="sidebar" :class="{ open: menuOpen }">
      <nav aria-label="主导航">
        <RouterLink
          v-for="item in navigation"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :class="{ active: route.path === item.to }"
          @click="menuOpen = false"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </RouterLink>
        <RouterLink
          v-if="!state.user"
          class="nav-item mobile-login-nav"
          to="/login"
          @click="menuOpen = false"
        >
          <LogIn :size="18" />
          <span>控制台登录</span>
        </RouterLink>
      </nav>

      <div v-if="state.user" class="account-block">
        <div class="avatar">{{ state.user.display_name.slice(0, 1).toUpperCase() }}</div>
        <div class="account-copy">
          <strong>{{ state.user.display_name }}</strong>
          <span>{{ state.user.role }}</span>
        </div>
        <button
          class="icon-button tooltip"
          type="button"
          data-tooltip="退出所有设备"
          aria-label="退出所有设备"
          @click="signOutAll"
        >
          <LogOut :size="17" />
        </button>
        <button
          class="icon-button tooltip"
          type="button"
          data-tooltip="退出登录"
          aria-label="退出登录"
          @click="signOut"
        >
          <LogOut :size="17" />
        </button>
      </div>
    </aside>

    <main class="main-content">
      <RouterView />
    </main>
    <button
      v-if="menuOpen"
      class="menu-scrim"
      type="button"
      aria-label="关闭菜单"
      @click="menuOpen = false"
    />
  </div>
</template>
