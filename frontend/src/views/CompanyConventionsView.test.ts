// @vitest-environment jsdom

import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '@/auth'
import CompanyConventionsView from '@/views/CompanyConventionsView.vue'

const apiGet = vi.fn()
const apiPatch = vi.fn()
const apiPost = vi.fn()

vi.mock('@/api', () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
    post: (...args: unknown[]) => apiPost(...args),
  },
  errorMessage: (error: unknown) => error instanceof Error ? error.message : '请求失败',
}))

const space = {
  id: 'space-1',
  workspace_id: 'workspace-1',
  name: 'Frontend',
  description: 'Frontend standards',
  visibility: 'workspace',
  role: 'viewer',
}

const repository = {
  id: 'repo-1',
  name: 'design-system',
  description: '',
  space_id: space.id,
  git_url: 'https://github.com/example/design-system.git',
  branch: 'main',
  visibility: 'private',
  license_name: '',
  license_url: '',
  status: 'ready',
  chunk_count: 20,
  last_commit: 'a'.repeat(40),
  last_indexed_at: '2026-09-04T00:00:00Z',
}

const convention = {
  id: 'convention-1',
  space_id: space.id,
  title: '组件使用 PascalCase',
  category: 'naming',
  language: 'typescript',
  framework: 'vue',
  task: 'component',
  rule: 'Vue 组件文件和导出名称统一使用 PascalCase。',
  prohibited_pattern: '不要使用 snake_case 组件名。',
  examples: ['UserProfile.vue'],
  citations: [{
    repository_id: repository.id,
    commit: repository.last_commit,
    path: 'src/components/UserProfile.vue',
    symbol: 'UserProfile',
    start_line: 1,
    end_line: 20,
  }],
  status: 'confirmed',
  updated_at: '2026-09-04T00:00:00Z',
}

function mountView(role: 'admin' | 'member') {
  const { state } = useAuth()
  state.user = {
    id: `${role}-1`,
    email: `${role}@example.com`,
    display_name: role,
    role,
    is_active: true,
    created_at: '2026-09-04T00:00:00Z',
  }
  state.csrfToken = 'csrf-test'
  state.initialized = true
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return mount(CompanyConventionsView, {
    global: {
      directives: { modalDialog: {} },
      plugins: [[VueQueryPlugin, { queryClient }]],
    },
  })
}

beforeEach(() => {
  apiGet.mockImplementation(async (url: string) => {
    if (url === '/spaces') return { data: [space] }
    if (url === '/repositories') return { data: [repository] }
    if (url === '/company-conventions') return { data: [convention] }
    throw new Error(`Unexpected GET ${url}`)
  })
  apiPatch.mockResolvedValue({ data: convention })
  apiPost.mockResolvedValue({ data: convention })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('CompanyConventionsView', () => {
  it('loads the selected space and keeps member access read-only', async () => {
    const wrapper = mountView('member')
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain(convention.title)
    expect(wrapper.text()).toContain(convention.rule)
    expect(wrapper.find('button[aria-label="编辑规范"]').exists()).toBe(false)
    expect(wrapper.findAll('button').some((button) => button.text().includes('新增规范'))).toBe(false)
    expect(apiGet).toHaveBeenCalledWith('/company-conventions', {
      params: {
        space_id: space.id,
        language: undefined,
        framework: undefined,
        task: undefined,
      },
    })
  })

  it('exposes source-backed editing controls to administrators', async () => {
    const wrapper = mountView('admin')
    await flushPromises()
    await flushPromises()

    const createButton = wrapper.findAll('button')
      .find((button) => button.text().includes('新增规范'))
    expect(createButton).toBeDefined()
    expect(wrapper.get('button[aria-label="编辑规范"]')).toBeTruthy()

    await createButton!.trigger('click')
    expect(wrapper.get('[role="dialog"]').attributes('aria-label')).toBe('新增规范')
    expect((wrapper.get('select').element as HTMLSelectElement).value).toBe(space.id)
  })
})
