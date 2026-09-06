// @vitest-environment jsdom

import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CodePreview from '@/components/CodePreview.vue'
import type { FilePreview, SearchResult } from '@/types'

const apiGet = vi.fn()
vi.mock('@/api', () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
  errorMessage: (error: unknown) => error instanceof Error ? error.message : '请求失败',
}))

const result: SearchResult = {
  repo: 'repo-1', generation_id: 'generation-a', commit: 'commit-a', path: 'src/main.ts',
  language: 'typescript', symbol: 'main', start_line: 25, end_line: 30,
  score: 1, vector_score: 1, lexical_score: 1, retrieval: 'hybrid', snippet: 'original source',
}

function preview(commit: string): FilePreview {
  return {
    repo: result.repo, commit, path: result.path, start_line: 5, end_line: 70,
    content: `source from ${commit}`,
  }
}

function mountPreview() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = mount(CodePreview, {
    props: { result },
    global: {
      directives: { modalDialog: {} },
      plugins: [[VueQueryPlugin, { queryClient }]],
    },
  })
  return { wrapper, queryClient }
}

beforeEach(() => {
  apiGet.mockReset()
  apiGet.mockResolvedValue({ data: preview(result.commit) })
})

describe('commit-bound code previews', () => {
  it('requests the cited commit and does not reuse another commit cache', async () => {
    const { wrapper, queryClient } = mountPreview()
    await flushPromises()
    expect(apiGet).toHaveBeenCalledWith('/repositories/repo-1/file', {
      params: { path: result.path, start_line: 5, end_line: 70, commit: result.commit },
      signal: expect.any(AbortSignal),
    })
    expect(wrapper.get('.source-code').text()).toContain('source from commit-a')
    expect(wrapper.find('a[href*="github.com/search"]').exists()).toBe(false)

    apiGet.mockResolvedValue({ data: preview('commit-b') })
    await wrapper.setProps({ result: { ...result, commit: 'commit-b' } })
    await flushPromises()

    expect(apiGet).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.source-code').text()).toContain('source from commit-b')
    expect(queryClient.getQueryCache().getAll()).toHaveLength(2)
    wrapper.unmount()
    queryClient.clear()
  })

  it('rejects content from a different commit even when the API returns success', async () => {
    apiGet.mockResolvedValue({ data: preview('newer-commit') })
    const { wrapper, queryClient } = mountPreview()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('引用版本已更新')
    expect(wrapper.find('.source-code').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('source from newer-commit')
    wrapper.unmount()
    queryClient.clear()
  })

  it('shows a version-unavailable error returned by the server', async () => {
    apiGet.mockRejectedValue(new Error('Cited commit is no longer available'))
    const { wrapper, queryClient } = mountPreview()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('Cited commit is no longer available')
    expect(wrapper.find('.source-code').exists()).toBe(false)
    wrapper.unmount()
    queryClient.clear()
  })

  it('keeps legacy citations without a commit readable', async () => {
    const { wrapper, queryClient } = mountPreview()
    await flushPromises()
    await wrapper.setProps({ result: { ...result, commit: '' } })
    await flushPromises()

    expect(apiGet.mock.lastCall?.[1].params.commit).toBeUndefined()
    expect(wrapper.get('.source-code').text()).toContain('source from commit-a')
    wrapper.unmount()
    queryClient.clear()
  })
})
