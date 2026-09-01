import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(fileURLToPath(new URL('./style.css', import.meta.url)), 'utf8')
const viteConfig = readFileSync(fileURLToPath(new URL('../vite.config.ts', import.meta.url)), 'utf8')
const blogLayout = readFileSync(
  fileURLToPath(new URL('../../blog/src/layouts/BaseLayout.astro', import.meta.url)),
  'utf8',
)

function compact(source: string) {
  return source.replace(/\s+/g, ' ').trim()
}

function mediaBlocks(source: string, marker: string) {
  const blocks: string[] = []
  let offset = 0
  while (true) {
    const start = source.indexOf(marker, offset)
    if (start < 0) break
    const next = source.indexOf('@media ', start + marker.length)
    blocks.push(source.slice(start, next < 0 ? source.length : next))
    offset = next < 0 ? source.length : next
  }
  if (!blocks.length) throw new Error(`Missing media query: ${marker}`)
  return compact(blocks.join('\n'))
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
  it('emits fonts as same-origin files compatible with the production CSP', () => {
    expect(compact(viteConfig)).toContain('assetsInlineLimit: 0')
  })

  it('keeps every standard form dialog body reachable inside the viewport', () => {
    const compactStylesheet = compact(stylesheet)
    const dialogShell = exactRuleBlock(compactStylesheet, '.form-dialog')
    const dialogForm = exactRuleBlock(compactStylesheet, '.form-dialog .stack-form')
    const allDialogShells = exactRuleBlock(
      compactStylesheet,
      '.code-preview, .form-dialog, .secret-dialog',
    )

    expectFallbackThenDynamicViewport(
      allDialogShells,
      'max-height: min(780px, calc(100vh - 48px));',
      'max-height: min(780px, calc(100dvh - 48px));',
    )
    expect(dialogShell).toContain('display: flex;')
    expect(dialogShell).toContain('flex-direction: column;')
    expect(dialogForm).toContain('min-height: 0;')
    expect(dialogForm).toContain('flex: 1 1 auto;')
    expect(dialogForm).toContain('overflow-y: auto;')
    expect(dialogForm).toContain('overscroll-behavior: contain;')
  })

  it('keeps search, filter, and submit controls in one touch-friendly row', () => {
    const mobile = mediaBlocks(stylesheet, '@media (max-width: 760px)')

    expect(mobile).toContain(
      '.search-form { grid-template-columns: 25px minmax(0, 1fr) 44px 44px; gap: 8px;',
    )
    expect(mobile).toContain(
      '.filter-button, .search-submit { width: 44px; min-width: 44px; height: 44px; min-height: 44px;',
    )
  })

  it('reflows source cards, actions, and section headings on phones', () => {
    const compactStylesheet = compact(stylesheet)
    const mobile = mediaBlocks(stylesheet, '@media (max-width: 760px)')
    const globalSourceCard = exactRuleBlock(compactStylesheet, '.source-card')
    const globalSourceError = exactRuleBlock(compactStylesheet, '.source-card .error-text')
    const globalDirectSourceError = exactRuleBlock(
      compactStylesheet,
      '.source-card > .error-text',
    )
    const globalSectionDescription = exactRuleBlock(
      compactStylesheet,
      '.section-heading > span',
    )
    const sourceBaseIndex = stylesheet.indexOf('.source-card-grid')
    const responsiveSourceStart = stylesheet.indexOf(
      '@media (max-width: 760px)',
      sourceBaseIndex,
    )
    const responsiveSourceEnd = stylesheet.indexOf('@media ', responsiveSourceStart + 1)
    const responsiveSourceRulesStart = stylesheet.indexOf('.source-card {', responsiveSourceStart)
    const responsiveSources = compact(
      stylesheet.slice(
        responsiveSourceRulesStart,
        responsiveSourceEnd < 0 ? stylesheet.length : responsiveSourceEnd,
      ),
    )
    const sourceCard = exactRuleBlock(responsiveSources, '.source-card')
    const sourceMain = exactRuleBlock(responsiveSources, '.source-card-main')
    const sourceMeta = exactRuleBlock(responsiveSources, '.source-card-meta')
    const sourceActions = exactRuleBlock(responsiveSources, '.source-card > .heading-actions')
    const sourceActionButtons = exactRuleBlock(
      responsiveSources,
      '.source-card > .heading-actions > button',
    )
    const directSourceError = exactRuleBlock(responsiveSources, '.source-card > .error-text')
    const sectionHeading = exactRuleBlock(mobile, '.section-heading')
    const sectionTitle = exactRuleBlock(mobile, '.section-heading > h2')
    const sectionDescription = exactRuleBlock(mobile, '.section-heading > span')
    const nestedSectionHeading = exactRuleBlock(mobile, '.section-heading > div')
    const nestedSectionDescription = exactRuleBlock(mobile, '.section-heading > div > span')
    const projectRow = exactRuleBlock(responsiveSources, '.gitlab-project-row')
    const desktopProjectRow = exactRuleBlock(stylesheet, '.gitlab-project-row')
    const desktopProjectLink = exactRuleBlock(stylesheet, '.gitlab-project-link')
    const desktopProjectName = exactRuleBlock(stylesheet, '.gitlab-project-link strong')
    const desktopProjectBranch = exactRuleBlock(stylesheet, '.gitlab-project-row > .mono-cell')
    const projectTrailingCells = exactRuleBlock(
      responsiveSources,
      '.gitlab-project-row > :nth-child(n + 3)',
    )
    const projectImport = exactRuleBlock(responsiveSources, '.gitlab-project-import')

    expect(globalSourceCard).toContain('flex-wrap: wrap;')
    expect(globalSourceError).toContain('min-width: 0;')
    expect(globalSourceError).toContain('max-width: 100%;')
    expect(globalSourceError).toContain('white-space: normal;')
    expect(globalSourceError).toContain('overflow-wrap: anywhere;')
    expect(globalDirectSourceError).toContain('flex-basis: 100%;')
    expect(globalSectionDescription).toContain('min-width: 0;')
    expect(globalSectionDescription).toContain('overflow-wrap: anywhere;')
    expect(responsiveSourceStart).toBeGreaterThan(sourceBaseIndex)
    expect(sourceCard).toContain('grid-template-columns: 36px minmax(0, 1fr);')
    expect(sourceMain).toContain('min-width: 0;')
    expect(sourceMeta).toContain('grid-column: 2;')
    expect(sourceMeta).toContain('align-items: flex-start;')
    expect(sourceActions).toContain('grid-column: 1 / -1;')
    expect(sourceActions).toContain('justify-content: flex-end;')
    expect(sourceActionButtons).toContain('width: auto;')
    expect(directSourceError).toContain('grid-column: 1 / -1;')
    expect(sectionHeading).toContain('flex-wrap: wrap;')
    expect(sectionHeading).toContain('align-items: flex-start;')
    expect(sectionTitle).toContain('flex: 1 1 auto;')
    expect(sectionDescription).toContain('order: 3;')
    expect(sectionDescription).toContain('flex-basis: 100%;')
    expect(nestedSectionHeading).toContain('min-width: 0;')
    expect(nestedSectionHeading).toContain('flex: 1 1 calc(100% - 56px);')
    expect(nestedSectionHeading).toContain('display: grid;')
    expect(nestedSectionDescription).toContain('overflow-wrap: anywhere;')
    expect(projectRow).toContain('grid-template-columns: 24px minmax(0, 1fr);')
    expect(desktopProjectRow).toContain(
      'grid-template-columns: 24px minmax(0, 1fr) 100px auto;',
    )
    expect(desktopProjectLink).toContain('min-width: 0;')
    expect(desktopProjectLink).toContain('overflow-wrap: anywhere;')
    expect(desktopProjectName).toContain('overflow-wrap: anywhere;')
    expect(desktopProjectBranch).toContain('min-width: 0;')
    expect(desktopProjectBranch).toContain('overflow-wrap: anywhere;')
    expect(projectTrailingCells).toContain('grid-column: 2;')
    expect(projectTrailingCells).toContain('justify-self: start;')
    expect(projectImport).toContain('grid-column: 2;')
    expect(projectImport).toContain('justify-self: start;')
  })

  it('uses touch-safe navigation and dynamic mobile viewport heights', () => {
    const mobile = mediaBlocks(stylesheet, '@media (max-width: 760px)')
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
    expect(mobile).toContain(
      '.chat-session-delete { width: 44px; height: 44px; opacity: 1; }',
    )
  })

  it('keeps blog navigation and footer links touch-friendly on phones', () => {
    const blogMobile = mediaBlocks(blogLayout, '@media (max-width: 640px)')

    expect(blogMobile).toContain('.site-brand, .demo-link, .footer-links a { min-height: 44px; }')
  })
})
