export type McpClientTarget = 'codex' | 'claude' | 'json'

const TOKEN_ENVIRONMENT_REFERENCE = '${CODEATLAS_MCP_TOKEN}'

export function buildMcpInstallConfig(target: McpClientTarget, mcpUrl: string): string {
  if (target === 'codex') {
    return `codex mcp add codeatlas --url "${mcpUrl}" --bearer-token-env-var CODEATLAS_MCP_TOKEN`
  }
  if (target === 'claude') {
    return `claude mcp add --transport http --scope user codeatlas "${mcpUrl}" --header "Authorization: Bearer ${TOKEN_ENVIRONMENT_REFERENCE}"`
  }
  return JSON.stringify({
    mcpServers: {
      codeatlas: {
        type: 'http',
        url: mcpUrl,
        headers: { Authorization: `Bearer ${TOKEN_ENVIRONMENT_REFERENCE}` },
      },
    },
  }, null, 2)
}
