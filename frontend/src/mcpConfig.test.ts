import { describe, expect, it } from 'vitest'

import { buildMcpInstallConfig } from '@/mcpConfig'

describe('MCP client configuration', () => {
  it.each(['codex', 'claude', 'json'] as const)(
    'uses an environment variable instead of a plaintext token for %s',
    (target) => {
      const config = buildMcpInstallConfig(target, 'https://codeatlas.example.com/mcp')

      expect(config).toContain('CODEATLAS_MCP_TOKEN')
      expect(config).not.toContain('cat_example_secret')
      expect(config).toContain('https://codeatlas.example.com/mcp')
    },
  )
})
