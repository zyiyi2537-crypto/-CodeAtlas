// @vitest-environment jsdom

import { VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { api } from '@/api'
import { clearSession, login, logout, logoutAll, refreshSession, useAuth } from '@/auth'
import AppShell from '@/components/AppShell.vue'
import { queryClient, sessionSignal } from '@/sessionScope'
import type { Repository, SearchResult, User } from '@/types'
import LoginView from '@/views/LoginView.vue'
import SearchView from '@/views/SearchView.vue'

const { redirectToLogin } = vi.hoisted(() => ({ redirectToLogin: vi.fn() }))
vi.mock('@/router', () => ({
  router: {
    currentRoute: { value: { name: 'search', fullPath: '/' } },
    push: redirectToLogin,
  },
}))

const userA: User = {
  id: 'user-a', email: 'a@example.com', display_name: 'Account A', role: 'member',
  is_active: true, created_at: '2026-09-04T00:00:00Z',
}
const userB: User = { ...userA, id: 'user-b', email: 'b@example.com', display_name: 'Account B' }
const privateRepo: Repository = {
  id: 'private-repo', space_id: 'restricted', name: 'Confidential repository', description: '',
  git_url: 'https://example.com/private.git', branch: 'main', visibility: 'private',
  license_name: '', license_url: '', status: 'ready', chunk_count: 1,
  last_commit: 'abc123', last_indexed_at: null,
}
const privateResult: SearchResult = {
  repo: privateRepo.id, generation_id: 'generation-a', commit: 'abc123', path: 'private.ts',
  language: 'typescript', symbol: 'ConfidentialImplementation', start_line: 1, end_line: 2,
  score: 1, vector_score: 1, lexical_score: 1, retrieval: 'hybrid', snippet: 'private source code',
}

function response(config: InternalAxiosRequestConfig, data: unknown): AxiosResponse {
  return { config, data, status: 200, statusText: 'OK', headers: {} }
}

function failedResponse(config: InternalAxiosRequestConfig, status: number): AxiosError {
  return new AxiosError('Request failed', 'ERR_BAD_RESPONSE', config, undefined, {
    ...response(config, { detail: 'Request failed' }), status,
  })
}

const originalAdapter = api.defaults.adapter
const wrappers: VueWrapper[] = []

beforeEach(() => {
  clearSession()
  queryClient.setDefaultOptions({ queries: { retry: false }, mutations: { retry: false } })
  const { state } = useAuth()
  state.user = userA
  state.csrfToken = 'csrf-a'
  state.initialized = true
  redirectToLogin.mockReset()
  api.defaults.adapter = async (config) => {
    if (config.url === '/auth/login' || config.url === '/auth/me') {
      return response(config, { user: userB, csrf_token: 'csrf-b' })
    }
    if (config.url === '/repositories') {
      return response(config, state.user?.id === userA.id ? [privateRepo] : [])
    }
    if (config.url === '/search') return response(config, [privateResult])
    return response(config, null)
  }
})

afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount()
  clearSession()
  api.defaults.adapter = originalAdapter
})

async function mountApp(path = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: SearchView },
      { path: '/login', name: 'login', component: LoginView },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  const wrapper = mount(AppShell, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('session data isolation', () => {
  it.each([logout, logoutAll])('clears queries and mutations when signing out', async (signOut) => {
    queryClient.setQueryData(['user-memories'], ['private memory'])
    const mutation = queryClient.getMutationCache().build(queryClient, {
      mutationFn: async () => 'private search result',
    })
    await mutation.execute(undefined)
    const signal = sessionSignal()

    await signOut()

    expect(useAuth().state.user).toBeNull()
    expect(useAuth().state.csrfToken).toBe('')
    expect(signal.aborted).toBe(true)
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0)
  })

  it('removes a private search result on logout even when the route stays unchanged', async () => {
    const wrapper = await mountApp()
    await wrapper.get('input[name="query"]').setValue('private')
    await wrapper.get('.search-form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain(privateResult.symbol)

    await wrapper.get('button[aria-label="退出登录"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain(privateResult.symbol)
    expect(wrapper.text()).not.toContain(privateRepo.name)
    expect(wrapper.get('input[name="query"]').element).toHaveProperty('value', '')

    await login(userB.email, 'password')
    await flushPromises()
    expect(wrapper.text()).toContain(userB.display_name)
    expect(wrapper.text()).not.toContain(privateResult.symbol)
    expect(wrapper.text()).not.toContain(privateRepo.name)
  })

  it('does not render a search response arriving after logout', async () => {
    const baseAdapter = api.defaults.adapter as (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>
    let finishSearch: (() => void) | undefined
    let searchSignal: InternalAxiosRequestConfig['signal']
    api.defaults.adapter = (config) => {
      if (config.url !== '/search') return baseAdapter(config)
      searchSignal = config.signal
      return new Promise((resolve) => {
        finishSearch = () => resolve(response(config, [privateResult]))
      })
    }
    const wrapper = await mountApp()
    await wrapper.get('input[name="query"]').setValue('private')
    await wrapper.get('.search-form').trigger('submit')
    await vi.waitFor(() => expect(finishSearch).toBeDefined())

    await wrapper.get('button[aria-label="退出登录"]').trigger('click')
    await flushPromises()
    expect(searchSignal?.aborted).toBe(true)
    finishSearch!()
    await flushPromises()

    expect(wrapper.text()).not.toContain(privateResult.symbol)
    expect(wrapper.find('.results-section').exists()).toBe(false)
  })

  it.each([login.bind(null, userB.email, 'password'), refreshSession])(
    'discards previous account caches when applying a session', async (apply) => {
      queryClient.setQueryData(['chat-sessions'], ['Account A conversation'])
      queryClient.setQueryData(['user-memories'], ['Account A memory'])
      await apply()
      expect(useAuth().state.user?.id).toBe(userB.id)
      expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
    },
  )

  it('ignores an old unauthorized response after another account logs in', async () => {
    const baseAdapter = api.defaults.adapter as (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>
    let rejectOldRequest: (() => void) | undefined
    api.defaults.adapter = (config) => {
      if (config.url !== '/private') return baseAdapter(config)
      return new Promise((_, reject) => {
        rejectOldRequest = () => reject(failedResponse(config, 401))
      })
    }
    const pending = api.get('/private').catch((error: unknown) => error)
    await vi.waitFor(() => expect(rejectOldRequest).toBeDefined())
    await login(userB.email, 'password')

    rejectOldRequest!()
    expect(axios.isCancel(await pending)).toBe(true)
    expect(useAuth().state.user?.id).toBe(userB.id)
    expect(redirectToLogin).not.toHaveBeenCalled()
  })

  it('does not let a delayed session probe clear a newly logged-in account', async () => {
    const baseAdapter = api.defaults.adapter as (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>
    let finishProbe: (() => void) | undefined
    api.defaults.adapter = (config) => {
      if (config.url !== '/auth/me') return baseAdapter(config)
      return new Promise((resolve) => {
        finishProbe = () => resolve(response(config, { user: userA, csrf_token: 'old' }))
      })
    }
    const pending = refreshSession()
    await vi.waitFor(() => expect(finishProbe).toBeDefined())
    await login(userB.email, 'password')
    finishProbe!()
    await pending
    expect(useAuth().state.user?.id).toBe(userB.id)
    expect(useAuth().state.csrfToken).toBe('csrf-b')
  })

  it('clears cached account data on a current unauthorized response', async () => {
    queryClient.setQueryData(['user-memories'], ['private memory'])
    api.defaults.adapter = async (config) => { throw failedResponse(config, 401) }
    await expect(api.get('/private')).rejects.toBeInstanceOf(AxiosError)

    expect(useAuth().state.user).toBeNull()
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
    expect(redirectToLogin).toHaveBeenCalledWith({ name: 'login', query: { redirect: '/' } })
  })

  it('clears stale account data when the session probe fails', async () => {
    queryClient.setQueryData(['user-memories'], ['private memory'])
    api.defaults.adapter = async (config) => { throw failedResponse(config, 401) }
    await refreshSession()
    expect(useAuth().state.user).toBeNull()
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
    expect(redirectToLogin).not.toHaveBeenCalled()
  })

  it('keeps the login form and its error visible when credentials are rejected', async () => {
    clearSession()
    api.defaults.adapter = async (config) => { throw failedResponse(config, 401) }
    const wrapper = await mountApp('/login')
    await wrapper.get('input[type="email"]').setValue(userB.email)
    await wrapper.get('input[type="password"]').setValue('invalid-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.error-banner').text()).toContain('Request failed')
    expect(wrapper.get('input[type="email"]').element).toHaveProperty('value', userB.email)
    expect(redirectToLogin).not.toHaveBeenCalled()
  })
})
