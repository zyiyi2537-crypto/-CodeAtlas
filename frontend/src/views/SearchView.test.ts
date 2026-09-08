// @vitest-environment jsdom

import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SearchResult } from '@/types'
import SearchView from '@/views/SearchView.vue'

const apiGet = vi.fn()
const apiPost = vi.fn()
vi.mock('@/api', () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
  },
  errorMessage: (error: unknown) => error instanceof Error ? error.message : '请求失败',
}))

const result: SearchResult = {
  repo: 'repo-1', generation_id: 'generation-1', commit: 'abc123', path: 'src/search.ts',
  language: 'typescript', symbol: 'search', start_line: 1, end_line: 2,
  score: 0.5, vector_score: 0, lexical_score: 1, retrieval: 'lexical', snippet: 'function search() {}',
}
const degradedResult = {
  ...result, path: 'src/fallback.ts', degraded: true, degradation_reason: 'embedding_unavailable',
}
let wrapper: VueWrapper
let queryClient: QueryClient

beforeEach(() => {
  apiGet.mockResolvedValue({ data: [] })
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  wrapper = mount(SearchView, { global: { plugins: [[VueQueryPlugin, { queryClient }]] } })
})

afterEach(() => {
  wrapper.unmount()
  queryClient.clear()
  vi.resetAllMocks()
})

async function search(results: SearchResult[]) {
  apiPost.mockResolvedValueOnce({ data: results })
  await wrapper.get('input[name="query"]').setValue('search')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  await vi.waitFor(() => expect(wrapper.find('.loading-block').exists()).toBe(false))
}

async function startSearch() {
  await wrapper.get('input[name="query"]').setValue('search')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

describe('search retrieval degradation', () => {
  it('explains the embedding outage and keyword fallback without exposing internal reason codes', async () => {
    await search([degradedResult])

    expect(wrapper.get('[role="status"]').text()).toContain('向量服务暂不可用，已使用关键词检索')
    expect(wrapper.get('[role="status"]').text()).not.toContain('embedding_unavailable')
  })

  it.each([
    ['empty results', []],
    ['normal lexical results', [result]],
    ['explicitly healthy results', [{ ...result, degraded: false, degradation_reason: 'embedding_unavailable' }]],
    ['reason without a degradation flag', [{ ...result, degradation_reason: 'embedding_unavailable' }]],
  ])('does not infer degradation from %s', async (_label, results) => {
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    await search(results)
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
  })

  it.each([
    ['normal results', [result]],
    ['empty results', []],
  ])('clears a previous degradation notice after %s', async (_label, results) => {
    await search([degradedResult])
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
    await search(results)
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.findAll('.result-row')).toHaveLength(results.length)
  })

  it('clears a previous degradation notice while the next search is pending', async () => {
    await search([degradedResult])
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
    apiPost.mockImplementationOnce(() => new Promise(() => {}))

    await startSearch()

    expect(wrapper.find('.loading-block').exists()).toBe(true)
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
  })

  it('does not retain a previous degradation notice when the next search fails', async () => {
    await search([degradedResult])
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
    apiPost.mockRejectedValueOnce(new Error('检索请求失败'))

    await startSearch()

    await vi.waitFor(() => expect(wrapper.find('.error-banner').exists()).toBe(true))
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
  })

  it.each([undefined, 'unknown_reason'])('shows a generic status for an unspecified or unknown reason (%s)', async (reason) => {
    await search([{ ...result, degraded: true, degradation_reason: reason }])
    expect(wrapper.get('[role="status"]').text()).toContain('检索已降级')
    expect(wrapper.get('[role="status"]').text()).not.toContain('向量服务')
    expect(wrapper.get('[role="status"]').text()).not.toContain('unknown_reason')
  })

  it('shows a Chinese status when any actual result is degraded, without hiding results', async () => {
    await search([result, degradedResult])

    expect(wrapper.find('[role="status"]').exists()).toBe(true)
    expect(wrapper.get('[role="status"]').text()).toContain('检索已降级')
    expect(wrapper.findAll('.result-row')).toHaveLength(2)
    expect(wrapper.text()).toContain('src/fallback.ts')
    expect(apiPost).toHaveBeenCalledWith('/search', {
      query: 'search', repository_ids: [], languages: [], path_prefix: '', limit: 10,
    })
  })
})
