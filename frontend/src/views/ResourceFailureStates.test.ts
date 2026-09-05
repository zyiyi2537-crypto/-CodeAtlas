// @vitest-environment jsdom

import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '@/auth'
import DocumentsView from '@/views/DocumentsView.vue'
import RepositoriesView from '@/views/RepositoriesView.vue'
import TokensView from '@/views/TokensView.vue'

const apiDelete = vi.fn()
const apiGet = vi.fn()
const apiPost = vi.fn()

vi.mock('@/api', () => ({
  api: {
    delete: (...args: unknown[]) => apiDelete(...args),
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
  },
  errorMessage: (error: unknown) => error instanceof Error ? error.message : '请求失败',
}))

function mountView(component: object) {
  const { state } = useAuth()
  state.user = {
    id: 'owner-1',
    email: 'owner@example.com',
    display_name: 'Owner',
    role: 'owner',
    is_active: true,
    created_at: '2026-09-04T00:00:00Z',
  }
  state.csrfToken = 'csrf-test'
  state.initialized = true
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return mount(component, {
    global: {
      directives: { modalDialog: {} },
      plugins: [[VueQueryPlugin, { queryClient }]],
    },
  })
}

beforeEach(() => {
  apiGet.mockRejectedValue(new Error('资源范围加载失败'))
  apiPost.mockResolvedValue({ data: {} })
  apiDelete.mockResolvedValue({ data: null })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('resource management failure states', () => {
  it.each([
    [TokensView, '暂无 Token'],
    [RepositoriesView, '暂无仓库'],
    [DocumentsView, '暂无文档集'],
  ])('shows a retryable query error instead of a false empty state', async (component, emptyText) => {
    const wrapper = mountView(component)
    await flushPromises()
    await flushPromises()

    expect(wrapper.get('[data-query-error]').text()).toContain('资源范围加载失败')
    expect(wrapper.text()).not.toContain(emptyText)

    await wrapper.get('[data-query-retry]').trigger('click')
    expect(apiGet.mock.calls.length).toBeGreaterThan(1)
  })

  it.each([
    [TokensView, '创建 Token'],
    [RepositoriesView, '新增仓库'],
    [DocumentsView, '新建文档集'],
  ])('blocks creation while authorization scope queries are unavailable', async (component, label) => {
    const wrapper = mountView(component)
    await flushPromises()
    const openButton = wrapper.findAll('button').find((button) => button.text().includes(label))
    expect(openButton).toBeDefined()
    await openButton!.trigger('click')
    await flushPromises()

    for (const input of wrapper.findAll('input[required]')) {
      await input.setValue(input.attributes('type') === 'url' ? 'https://github.com/example/repo.git' : 'valid-name')
    }

    expect(wrapper.get('[data-scope-error]').text()).toContain('资源范围加载失败')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('shows a document query failure instead of an empty collection', async () => {
    apiGet.mockImplementation(async (url: string) => {
      if (url === '/document-collections') {
        return { data: [{ id: 'collection-1', name: 'Runbooks', description: '', space_id: 'space-1' }] }
      }
      if (url === '/spaces') {
        return { data: [{ id: 'space-1', name: 'Default' }] }
      }
      if (url === '/document-collections/collection-1/documents') {
        throw new Error('文档列表加载失败')
      }
      throw new Error(`Unexpected GET ${url}`)
    })
    const wrapper = mountView(DocumentsView)
    await flushPromises()
    await wrapper.get('.source-card').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.get('[data-document-error]').text()).toContain('文档列表加载失败')
    expect(wrapper.text()).not.toContain('文档集为空')
  })
})
