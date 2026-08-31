// @vitest-environment jsdom

import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '@/auth'
import ChatView from '@/views/ChatView.vue'

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiDelete = vi.fn()
const apiPatch = vi.fn()

vi.mock('@/api', () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    delete: (...args: unknown[]) => apiDelete(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
  },
  errorMessage: (error: unknown) => error instanceof Error ? error.message : '请求失败',
}))

const sessionSummary = {
  id: 'session-1',
  title: '登录故障排查',
  repository_ids: [],
  created_at: '2026-08-30T10:00:00Z',
  updated_at: '2026-08-30T10:05:00Z',
}

const memory = {
  id: 'memory-1',
  kind: 'preference',
  content: '用户偏好用中文解释代码调用链。',
  created_at: '2026-08-30T09:00:00Z',
  updated_at: '2026-08-30T09:00:00Z',
}

function responseFor(url: string) {
  if (url === '/repositories') return []
  if (url === '/chat/status') return { enabled: true, model: 'test', provider: 'test' }
  if (url === '/chat/sessions') return [sessionSummary]
  if (url === '/chat/sessions/session-1') {
    return {
      ...sessionSummary,
      messages: [
        {
          id: 'message-1',
          role: 'user',
          content: '登录入口在哪里？',
          citations: [],
          created_at: '2026-08-30T10:01:00Z',
        },
      ],
    }
  }
  if (url === '/memories') return [memory]
  if (url === '/llm/providers') {
    return [{
      id: 'provider-1',
      name: 'gpt',
      base_url: 'https://api.example.com/v1',
      model: 'gpt-test',
      models: [],
      is_active: true,
      api_key_configured: true,
      last_synced_at: null,
    }]
  }
  throw new Error(`Unexpected GET ${url}`)
}

function mountChat(role: 'admin' | 'member') {
  const { state } = useAuth()
  state.user = {
    id: `${role}-1`,
    email: `${role}@example.com`,
    display_name: role === 'admin' ? 'Administrator' : 'Member',
    role,
    is_active: true,
    created_at: '2026-08-30T00:00:00Z',
  }
  state.csrfToken = 'csrf-test'
  state.initialized = true
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return mount(ChatView, {
    global: {
      plugins: [[VueQueryPlugin, { queryClient }]],
    },
  })
}

beforeEach(() => {
  apiGet.mockImplementation(async (url: string) => ({ data: responseFor(url) }))
  apiPost.mockResolvedValue({ data: {} })
  apiDelete.mockResolvedValue({ data: null })
  apiPatch.mockResolvedValue({ data: {} })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('ChatView account workspace', () => {
  it('shows account-scoped conversation history and persistent memory', async () => {
    const wrapper = mountChat('member')
    await flushPromises()
    await flushPromises()

    expect(wrapper.get('.chat-history-panel').text()).toContain('登录故障排查')
    expect(wrapper.get('.chat-memory-panel').text()).toContain('长期记忆')
    expect(wrapper.get('.chat-memory-panel').text()).toContain('用户偏好用中文解释代码调用链。')
    expect(wrapper.get('.chat-thread').text()).toContain('登录入口在哪里？')
  })

  it('preserves structured document and Wiki citation coordinates', async () => {
    apiGet.mockImplementation(async (url: string) => {
      if (url !== '/chat/sessions/session-1') return { data: responseFor(url) }
      const citation = {
        source_id: 'source-1',
        section: '',
        repo: '',
        path: '',
        symbol: '',
        start_line: 0,
        end_line: 0,
        external_provider: '',
        external_source_id: '',
        external_id: '',
        source_url: '',
        structure_type: '',
        row_end: null,
        sources: [],
      }
      return {
        data: {
          ...sessionSummary,
          messages: [{
            id: 'assistant-citations',
            role: 'assistant',
            content: '结构化答案',
            created_at: '2026-08-30T10:01:00Z',
            citations: [
              { ...citation, source_type: 'document', title: 'SLA台账', sheet: 'SLA矩阵', row_start: 17, row_end: 21, slide: null, page: null },
              { ...citation, source_type: 'document', title: '技术评审', sheet: '', row_start: null, slide: 3, page: null },
              { ...citation, source_type: 'document', title: '运维手册', sheet: '', row_start: null, slide: null, page: 5 },
              { ...citation, source_type: 'wiki', title: '事故整改', sheet: '', row_start: null, slide: null, page: null, sources: ['source-a', 'source-b'] },
            ],
          }],
        },
      }
    })
    const wrapper = mountChat('member')
    await flushPromises()
    await flushPromises()

    const citations = wrapper.get('.citation-list').text()
    expect(citations).toContain('工作表 SLA矩阵')
    expect(citations).toContain('行 17–21')
    expect(citations).toContain('第 3 张幻灯片')
    expect(citations).toContain('第 5 页')
    expect(citations).toContain('2 条来源')
  })

  it('creates account memory through the protected API', async () => {
    const wrapper = mountChat('member')
    await flushPromises()
    await wrapper.get('select[aria-label="记忆类型"]').setValue('constraint')
    await wrapper.get('textarea[aria-label="记忆内容"]').setValue('回答必须附带来源。')
    await wrapper.get('.memory-create-form').trigger('submit')
    await flushPromises()

    expect(apiPost).toHaveBeenCalledWith(
      '/memories',
      { kind: 'constraint', content: '回答必须附带来源。' },
      { headers: { 'X-CSRF-Token': 'csrf-test' } },
    )
  })

  it('creates a persistent conversation before sending the first message', async () => {
    apiPost.mockImplementation(async (url: string) => {
      if (url === '/chat/sessions') return { data: { ...sessionSummary, id: 'session-new' } }
      if (url === '/chat/sessions/session-new/messages') {
        return { data: { answer: '持久回答', citations: [] } }
      }
      return { data: {} }
    })
    const wrapper = mountChat('member')
    await flushPromises()
    await wrapper.get('.chat-new-session').trigger('click')
    await wrapper.get('textarea[aria-label="提问内容"]').setValue('分析登录流程')
    await wrapper.get('.chat-composer').trigger('submit')
    await vi.waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        '/chat/sessions/session-new/messages',
        { question: '分析登录流程' },
        { headers: { 'X-CSRF-Token': 'csrf-test' } },
      )
    })
    await flushPromises()

    expect(apiPost).toHaveBeenCalledWith(
      '/chat/sessions',
      { title: '分析登录流程', repository_ids: [] },
      { headers: { 'X-CSRF-Token': 'csrf-test' } },
    )
    expect(wrapper.get('.chat-thread').text()).toContain('持久回答')
  })

  it('restores the draft and removes optimistic history when sending fails', async () => {
    apiPost.mockImplementation(async (url: string) => {
      if (url === '/chat/sessions') return { data: { ...sessionSummary, id: 'session-failed' } }
      if (url === '/chat/sessions/session-failed/messages') throw new Error('上游模型不可用')
      return { data: {} }
    })
    const wrapper = mountChat('member')
    await flushPromises()
    await wrapper.get('.chat-new-session').trigger('click')
    const input = wrapper.get('textarea[aria-label="提问内容"]')
    await input.setValue('请保留这个问题')
    await wrapper.get('.chat-composer').trigger('submit')
    await vi.waitFor(() => {
      expect(wrapper.get('.error-banner').text()).toContain('上游模型不可用')
    })

    expect((input.element as HTMLTextAreaElement).value).toBe('请保留这个问题')
    expect(wrapper.findAll('.chat-message.user')).toHaveLength(0)
  })

  it('presents model configuration as a structured scrollable dialog', async () => {
    const topbar = document.createElement('header')
    topbar.className = 'topbar'
    const sidebar = document.createElement('aside')
    sidebar.className = 'sidebar'
    const menuScrim = document.createElement('button')
    menuScrim.className = 'menu-scrim'
    document.body.append(topbar, sidebar, menuScrim)
    const wrapper = mountChat('admin')
    document.body.appendChild(wrapper.element)
    await flushPromises()
    await wrapper.get('button[data-testid="open-model-settings"]').trigger('click')
    await flushPromises()

    const dialog = wrapper.get('.model-settings-dialog')
    expect(dialog.attributes('aria-label')).toBe('模型配置')
    expect(dialog.get('.model-settings-body').text()).toContain('连接设置')
    expect(dialog.get('.model-provider-card').text()).toContain('gpt')
    expect(dialog.text()).toContain('密钥不会回显')
    expect(dialog.get('.provider-active-badge').element.tagName).toBe('SPAN')
    expect(dialog.get('.provider-active-badge').text()).toContain('当前使用')
    expect(dialog.findAll('button').some((button) => button.text().includes('已启用'))).toBe(false)
    expect(dialog.get('.model-form-actions .command-button').text()).toContain('保存并启用')
    expect(document.body.style.overflow).toBe('hidden')
    expect(topbar.inert).toBe(true)
    expect(sidebar.inert).toBe(true)
    expect(menuScrim.inert).toBe(true)
    const closeButton = dialog.get('button[aria-label="关闭模型配置"]').element as HTMLButtonElement
    expect(document.activeElement).toBe(closeButton)

    const dialogButtons = dialog.findAll<HTMLButtonElement>('button:not([disabled])')
    const lastButton = dialogButtons.at(-1)?.element
    expect(lastButton).toBeTruthy()
    lastButton?.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(closeButton)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))
    expect(document.activeElement).toBe(lastButton)

    await wrapper.get('.model-provider-card button[aria-label="编辑 gpt"]').trigger('click')
    expect(dialog.get('.model-form-actions .command-button').text()).toContain('保存修改')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.find('.model-settings-dialog').exists()).toBe(false)
    expect(document.body.style.overflow).toBe('')
    expect(topbar.inert).toBe(false)
    expect(sidebar.inert).toBe(false)
    expect(menuScrim.inert).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('button[data-testid="open-model-settings"]').element)
    wrapper.unmount()
    document.body.innerHTML = ''
  })
})
