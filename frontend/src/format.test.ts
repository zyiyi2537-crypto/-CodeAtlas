import { describe, expect, it } from 'vitest'

import { formatNumber, shortCommit } from '@/format'

describe('format helpers', () => {
  it('shortens commit identifiers', () => {
    expect(shortCommit('1234567890abcdef')).toBe('12345678')
    expect(shortCommit('')).toBe('—')
  })

  it('formats indexed chunk counts', () => {
    expect(formatNumber(12000)).toMatch(/12[,.]000/)
  })
})
