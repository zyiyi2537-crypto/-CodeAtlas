// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { useAuth } from '@/auth'
import AppShell from '@/components/AppShell.vue'

const EmptyView = { template: '<div />' }

describe('AppShell', () => {
  it('renders the CodeAtlas brand mark in the global header', async () => {
    const { state } = useAuth()
    state.user = null
    state.csrfToken = ''
    state.initialized = true

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: EmptyView }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(AppShell, {
      global: { plugins: [router] },
    })
    const brand = wrapper.get('a[aria-label="CodeAtlas 控制台首页"]')
    const logo = brand.get('img.brand-logo')

    expect(logo.attributes('src')).toContain('codeatlas-mark.svg')
    expect(logo.attributes('alt')).toBe('')
    expect(brand.text()).toContain('CodeAtlas')
  })

  it('renders every administration destination for an administrator', async () => {
    const { state } = useAuth()
    state.user = {
      id: 'admin-1',
      email: 'admin@example.com',
      display_name: 'Administrator',
      role: 'admin',
      is_active: true,
      created_at: '2026-08-23T00:00:00Z',
    }
    state.csrfToken = 'test-csrf'
    state.initialized = true

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: EmptyView },
        { path: '/:pathMatch(.*)*', component: EmptyView },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(AppShell, {
      global: { plugins: [router] },
    })
    const destinations = wrapper.findAll('.sidebar .nav-item')

    expect(destinations).toHaveLength(13)
    expect(destinations.at(-1)?.text()).toContain('API Token')
    expect(wrapper.text()).toContain('GitLab 来源')
    expect(wrapper.text()).toContain('GitHub 来源')
    expect(wrapper.text()).toContain('外部知识源')
  })

  it.each(['owner', 'workspace_admin'] as const)(
    'renders administration destinations for %s',
    async (role) => {
      const { state } = useAuth()
      state.user = {
        id: `${role}-1`,
        email: `${role}@example.com`,
        display_name: role,
        role,
        is_active: true,
        created_at: '2026-09-02T00:00:00Z',
      }
      state.csrfToken = 'test-csrf'
      state.initialized = true

      const router = createRouter({
        history: createMemoryHistory(),
        routes: [
          { path: '/', component: EmptyView },
          { path: '/:pathMatch(.*)*', component: EmptyView },
        ],
      })
      await router.push('/')
      await router.isReady()

      const wrapper = mount(AppShell, {
        global: { plugins: [router] },
      })

      expect(wrapper.text()).toContain('成员')
      expect(wrapper.text()).toContain('API Token')
    },
  )

  it('includes an anonymous login destination in the mobile navigation', async () => {
    const { state } = useAuth()
    state.user = null
    state.csrfToken = ''
    state.initialized = true

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: EmptyView },
        { path: '/login', component: EmptyView },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(AppShell, {
      global: { plugins: [router] },
    })

    expect(wrapper.get('.mobile-login-nav').text()).toContain('控制台登录')
  })
})
