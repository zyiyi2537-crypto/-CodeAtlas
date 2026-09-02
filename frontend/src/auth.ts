import { computed, reactive } from 'vue'

import { api } from '@/api'
import { isAdminRole, isOwnerRole } from '@/roles'
import type { User } from '@/types'

interface AuthState {
  user: User | null
  csrfToken: string
  initialized: boolean
}

const state = reactive<AuthState>({
  user: null,
  csrfToken: '',
  initialized: false,
})

function applySession(payload: { user: User; csrf_token: string }) {
  state.user = payload.user
  state.csrfToken = payload.csrf_token
}

export async function refreshSession(): Promise<void> {
  try {
    const { data } = await api.get<{ user: User; csrf_token: string }>('/auth/me')
    applySession(data)
  } catch {
    state.user = null
    state.csrfToken = ''
  } finally {
    state.initialized = true
  }
}

export async function login(email: string, password: string): Promise<void> {
  const { data } = await api.post<{ user: User; csrf_token: string }>('/auth/login', {
    email,
    password,
  })
  applySession(data)
  state.initialized = true
}

export async function logout(): Promise<void> {
  await api.post('/auth/logout', null, { headers: csrfHeaders() })
  state.user = null
  state.csrfToken = ''
}

export function csrfHeaders(): Record<string, string> {
  return state.csrfToken ? { 'X-CSRF-Token': state.csrfToken } : {}
}

export function useAuth() {
  return {
    state,
    isAuthenticated: computed(() => Boolean(state.user)),
    isAdmin: computed(() => isAdminRole(state.user?.role)),
    isOwner: computed(() => isOwnerRole(state.user?.role)),
  }
}
