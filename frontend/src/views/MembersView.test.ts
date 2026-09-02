// @vitest-environment jsdom

import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '@/auth'
import type { UserRole } from '@/roles'
import type { User } from '@/types'
import MembersView from '@/views/MembersView.vue'

const apiGet = vi.fn()
const apiPatch = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
const apiDelete = vi.fn()

vi.mock('@/api', () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
    post: (...args: unknown[]) => apiPost(...args),
    put: (...args: unknown[]) => apiPut(...args),
    delete: (...args: unknown[]) => apiDelete(...args),
  },
  errorMessage: (error: unknown) => error instanceof Error ? error.message : '请求失败',
}))

const owner: User = {
  id: 'owner-1',
  email: 'owner@example.com',
  display_name: 'Owner',
  role: 'owner',
  is_active: true,
  created_at: '2026-09-02T00:00:00Z',
}

const member: User = {
  id: 'member-1',
  email: 'member@example.com',
  display_name: 'Member',
  role: 'member',
  is_active: true,
  created_at: '2026-09-02T00:00:00Z',
}

function mountMembers(role: UserRole) {
  const { state } = useAuth()
  state.user = {
    ...(role === 'owner' ? owner : {
      id: 'workspace-admin-1',
      email: 'workspace-admin@example.com',
      display_name: 'Workspace admin',
      role,
      is_active: true,
      created_at: '2026-09-02T00:00:00Z',
    }),
  }
  state.csrfToken = 'csrf-test'
  state.initialized = true
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return mount(MembersView, {
    global: {
      directives: { modalDialog: {} },
      plugins: [[VueQueryPlugin, { queryClient }]],
    },
  })
}

beforeEach(() => {
  apiGet.mockImplementation(async (url: string) => {
    if (url === '/members') return { data: [owner, member] }
    if (url === '/repositories') return { data: [] }
    throw new Error(`Unexpected GET ${url}`)
  })
  apiPatch.mockResolvedValue({ data: { ...member, role: 'workspace_admin' } })
  apiPost.mockResolvedValue({ data: {} })
  apiPut.mockResolvedValue({ data: {} })
  apiDelete.mockResolvedValue({ data: null })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('MembersView role management', () => {
  it('lets an owner change an existing member role', async () => {
    const wrapper = mountMembers('owner')
    await flushPromises()

    const select = wrapper.get('[data-member-role="member-1"]')
    expect(select.element).toBeInstanceOf(HTMLSelectElement)
    expect((select.element as HTMLSelectElement).value).toBe('member')

    await select.setValue('workspace_admin')
    await vi.waitFor(() => {
      expect(apiPatch).toHaveBeenCalledWith(
        '/members/member-1',
        { role: 'workspace_admin' },
        { headers: { 'X-CSRF-Token': 'csrf-test' } },
      )
    })
  })

  it('does not expose role assignment controls to a workspace administrator', async () => {
    const wrapper = mountMembers('workspace_admin')
    await flushPromises()

    expect(wrapper.find('[data-member-role="member-1"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('成员')
  })
})
