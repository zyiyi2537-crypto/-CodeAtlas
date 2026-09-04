import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const modalFiles = [
  './views/CompanyConventionsView.vue',
  './views/DocumentsView.vue',
  './views/EmbeddingProfilesView.vue',
  './views/ExternalSourcesView.vue',
  './views/GitHubSourcesView.vue',
  './views/GitLabSourcesView.vue',
  './views/MembersView.vue',
  './views/RepositoriesView.vue',
  './views/TokensView.vue',
  './components/CodePreview.vue',
]

function source(relative: string) {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')
}

describe('modal template contract', () => {
  it.each(modalFiles)('%s applies the shared modal lifecycle to every dialog', (relative) => {
    const markup = source(relative)
    const dialogs = [...markup.matchAll(/<section\b[^>]*role="dialog"[^>]*>/g)].map((match) => match[0])
    expect(dialogs.length).toBeGreaterThan(0)
    for (const dialog of dialogs) {
      expect(dialog).toContain('v-modal-dialog')
      expect(dialog).toMatch(/aria-(?:label|labelledby)="[^"]+"/)
    }
  })

  it('keeps GitLab row navigation and import as sibling controls', () => {
    const markup = source('./views/GitLabSourcesView.vue')
    const row = markup.slice(markup.indexOf('class="gitlab-project-row"'), markup.indexOf('</a>', markup.indexOf('class="gitlab-project-row"')) + 4)
    expect(row).not.toContain('<button')
    expect(markup).toContain('gitlab-project-import')
  })

  it('keeps Codex MCP credentials out of config.toml', () => {
    const markup = source('./mcpConfig.ts')

    expect(markup).toContain('--bearer-token-env-var CODEATLAS_MCP_TOKEN')
    expect(markup).not.toContain('http_headers = { Authorization')
  })
})
