export const GITHUB_SSH_CLONE_PATTERN =
  'git@github\\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\\.git'
export const GITHUB_HTTPS_CLONE_PATTERN =
  'https://github\\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\\.git'

export function normalizeOpenSshPublicKey(value: string): string {
  const fields = value.trim().split(/\s+/)
  if (fields.length < 2) throw new Error('公钥格式不完整')
  const type = fields[0]!
  const payload = fields[1]!
  if (!type.startsWith('ssh-') || !/^[A-Za-z0-9+/]+={0,2}$/.test(payload)) {
    throw new Error('公钥不是有效的 OpenSSH 格式')
  }
  // GitHub only needs the key type and payload. Omitting the optional comment
  // avoids accidental visual line-wrap/newline corruption during manual copy.
  return `${type} ${payload}`
}

export function normalizeGitHubCloneUrl(
  value: string,
  visibility: 'public' | 'private',
): string {
  const normalized = value.trim()
  const httpsMatch = normalized.match(
    /^https:\/\/github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?$/i,
  )
  const sshMatch = normalized.match(
    /^git@github\.com:([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?$/i,
  )
  if (visibility === 'private' && httpsMatch) {
    return `git@github.com:${httpsMatch[1]}/${httpsMatch[2]}.git`
  }
  if (visibility === 'public' && sshMatch) {
    return `https://github.com/${sshMatch[1]}/${sshMatch[2]}.git`
  }
  return normalized
}

export async function copyText(value: string, input?: HTMLInputElement): Promise<void> {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(value)
    return
  }
  if (!input) throw new Error('当前浏览器不支持自动复制，请使用 HTTPS 后重试')
  input.focus()
  input.select()
  input.setSelectionRange(0, value.length)
  if (!document.execCommand('copy')) {
    throw new Error('自动复制失败，请按 Ctrl+C 复制已选中的公钥')
  }
}
