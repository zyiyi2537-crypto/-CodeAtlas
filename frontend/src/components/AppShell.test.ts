// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import AppShell from '@/components/AppShell.vue'

vi.mock('@/auth', () => ({
  logout: vi.fn(),
  useAuth: () => ({
    state: { user: null },
    isAdmin: { value: false },
  }),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/' }),
  useRouter: () => ({ push: vi.fn() }),
}))

describe('AppShell', () => {
  it('includes an anonymous login destination in the mobile navigation', () => {
    const wrapper = mount(AppShell, {
      global: {
        stubs: {
          RouterLink: { template: '<a><slot /></a>' },
          RouterView: true,
        },
      },
    })

    expect(wrapper.get('.mobile-login-nav').text()).toContain('控制台登录')
  })
})
