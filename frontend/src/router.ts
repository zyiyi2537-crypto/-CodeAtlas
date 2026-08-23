import { createRouter, createWebHistory } from 'vue-router'

import { refreshSession, useAuth } from '@/auth'
import JobsView from '@/views/JobsView.vue'
import BrowseView from '@/views/BrowseView.vue'
import ChatView from '@/views/ChatView.vue'
import DocumentsView from '@/views/DocumentsView.vue'
import EmbeddingProfilesView from '@/views/EmbeddingProfilesView.vue'
import GitLabSourcesView from '@/views/GitLabSourcesView.vue'
import GitHubSourcesView from '@/views/GitHubSourcesView.vue'
import LoginView from '@/views/LoginView.vue'
import MembersView from '@/views/MembersView.vue'
import NotFoundView from '@/views/NotFoundView.vue'
import OverviewView from '@/views/OverviewView.vue'
import RepositoriesView from '@/views/RepositoriesView.vue'
import SearchView from '@/views/SearchView.vue'
import TokensView from '@/views/TokensView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    requiresAdmin?: boolean
  }
}

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'search', component: SearchView },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/chat', name: 'chat', component: ChatView, meta: { requiresAuth: true } },
    { path: '/browse', name: 'browse', component: BrowseView, meta: { requiresAuth: true } },
    {
      path: '/gitlab-sources',
      name: 'gitlab-sources',
      component: GitLabSourcesView,
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/github-sources',
      name: 'github-sources',
      component: GitHubSourcesView,
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/documents',
      name: 'documents',
      component: DocumentsView,
      meta: { requiresAuth: true },
    },
    {
      path: '/embedding-profiles',
      name: 'embedding-profiles',
      component: EmbeddingProfilesView,
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/overview',
      name: 'overview',
      component: OverviewView,
      meta: { requiresAuth: true },
    },
    {
      path: '/repositories',
      name: 'repositories',
      component: RepositoriesView,
      meta: { requiresAdmin: true },
    },
    {
      path: '/jobs',
      name: 'jobs',
      component: JobsView,
      meta: { requiresAuth: true },
    },
    {
      path: '/members',
      name: 'members',
      component: MembersView,
      meta: { requiresAdmin: true },
    },
    {
      path: '/tokens',
      name: 'tokens',
      component: TokensView,
      meta: { requiresAdmin: true },
    },
    { path: '/:pathMatch(.*)*', component: NotFoundView },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const { state } = useAuth()
  if (!state.initialized) await refreshSession()
  if (to.name === 'login' && state.user) return { name: 'overview' }
  if ((to.meta.requiresAuth || to.meta.requiresAdmin) && !state.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && state.user?.role !== 'admin') return { name: 'search' }
  return true
})
