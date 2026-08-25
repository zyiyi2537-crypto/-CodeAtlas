import { describe, expect, it } from 'vitest'

import { normalizeGitHubCloneUrl, normalizeOpenSshPublicKey } from './sshKey'

describe('normalizeOpenSshPublicKey', () => {
  it('removes visual or pasted whitespace without changing key fields', () => {
    expect(
      normalizeOpenSshPublicKey(
        'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF1aCiuahvGs4Urr6GdXYTkE2PukAv6eLIIIRwNOGVOU codeatlas-\nb75a121a5e9847b3b28ebd5384f25f83',
      ),
    ).toBe('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF1aCiuahvGs4Urr6GdXYTkE2PukAv6eLIIIRwNOGVOU')
  })

  it('preserves a valid one-line key', () => {
    const key = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGbMkD/tAtOELO55/dgl6Xq9w6q+uPr6MlFsEYuZGvBR codeatlas-key'
    expect(normalizeOpenSshPublicKey(key)).toBe(
      'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGbMkD/tAtOELO55/dgl6Xq9w6q+uPr6MlFsEYuZGvBR',
    )
  })

  it('rejects incomplete values', () => {
    expect(() => normalizeOpenSshPublicKey('ssh-ed25519')).toThrow('公钥格式不完整')
  })
})

describe('normalizeGitHubCloneUrl', () => {
  it('converts a GitHub HTTPS clone URL to SSH', () => {
    expect(normalizeGitHubCloneUrl('https://github.com/Bytedesk/bytedesk.git')).toBe(
      'git@github.com:Bytedesk/bytedesk.git',
    )
  })

  it('preserves a GitHub SSH clone URL', () => {
    expect(normalizeGitHubCloneUrl('git@github.com:Bytedesk/bytedesk.git')).toBe(
      'git@github.com:Bytedesk/bytedesk.git',
    )
  })
})
