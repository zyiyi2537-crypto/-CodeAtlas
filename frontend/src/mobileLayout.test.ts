import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(fileURLToPath(new URL('./style.css', import.meta.url)), 'utf8')
const blogLayout = readFileSync(
  fileURLToPath(new URL('../../blog/src/layouts/BaseLayout.astro', import.meta.url)),
  'utf8',
)

function compact(source: string) {
  return source.replace(/\s+/g, ' ').trim()
}

function lastMediaBlock(source: string, marker: string) {
  const start = source.lastIndexOf(marker)
  if (start < 0) throw new Error(`Missing media query: ${marker}`)
  const next = source.indexOf('@media ', start + marker.length)
  return compact(source.slice(start, next < 0 ? source.length : next))
}

function exactRuleBlock(source: string, selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s*')
  const match = new RegExp(`(?:^|})\\s*${escaped}\\s*\\{([^}]*)}`).exec(source)
  if (!match?.[1]) throw new Error(`Missing exact rule: ${selector}`)
  return match[1]
}

function expectFallbackThenDynamicViewport(rule: string, fallback: string, dynamic: string) {
  const fallbackIndex = rule.indexOf(fallback)
  const dynamicIndex = rule.indexOf(dynamic)
  expect(fallbackIndex).toBeGreaterThanOrEqual(0)
  expect(dynamicIndex).toBeGreaterThan(fallbackIndex)
}

describe('mobile layout contract', () => {
  it('keeps search, filter, and submit controls in one touch-friendly row', () => {
    const mobile = lastMediaBlock(stylesheet, '@media (max-width: 760px)')

    expect(mobile).toContain(
      '.search-form { grid-template-columns: 25px minmax(0, 1fr) 44px 44px; gap: 8px;',
    )
    expect(mobile).toContain(
      '.filter-button, .search-submit { width: 44px; min-width: 44px; height: 44px; min-height: 44px;',
    )
  })

  it('uses touch-safe navigation and dynamic mobile viewport heights', () => {
    const mobile = lastMediaBlock(stylesheet, '@media (max-width: 760px)')
    const menuButton = exactRuleBlock(mobile, '.mobile-menu-button')
    const sidebar = exactRuleBlock(mobile, '.sidebar')
    const scrim = exactRuleBlock(mobile, '.menu-scrim')
    const dialogs = exactRuleBlock(mobile, '.code-preview, .form-dialog, .secret-dialog')
    const sourceCode = exactRuleBlock(mobile, '.source-code')

    expect(menuButton).toContain('width: 44px;')
    expect(menuButton).toContain('height: 44px;')
    expect(menuButton).toContain('min-height: 44px;')
    expect(menuButton).toContain('flex-basis: 44px;')
    expect(mobile).toContain('.nav-item { min-height: 44px; }')
    expect(mobile).toContain('.filter-row { grid-template-columns: 1fr 1fr 44px; }')
    expect(mobile).toContain('.filter-row > .icon-button { width: 44px; height: 44px; }')
    expect(sidebar).toContain('top: 56px;')
    expect(sidebar).toContain('bottom: auto;')
    expectFallbackThenDynamicViewport(
      sidebar,
      'height: calc(100vh - 56px);',
      'height: calc(100dvh - 56px);',
    )
    expect(scrim).toContain('inset: 56px 0 auto;')
    expectFallbackThenDynamicViewport(
      scrim,
      'height: calc(100vh - 56px);',
      'height: calc(100dvh - 56px);',
    )
    expectFallbackThenDynamicViewport(
      dialogs,
      'max-height: calc(100vh - 20px);',
      'max-height: calc(100dvh - 20px);',
    )
    expectFallbackThenDynamicViewport(
      sourceCode,
      'max-height: calc(100vh - 92px);',
      'max-height: calc(100dvh - 92px);',
    )
  })

  it('keeps blog navigation and footer links touch-friendly on phones', () => {
    const blogMobile = lastMediaBlock(blogLayout, '@media (max-width: 640px)')

    expect(blogMobile).toContain('.site-brand, .demo-link, .footer-links a { min-height: 44px; }')
  })
})
