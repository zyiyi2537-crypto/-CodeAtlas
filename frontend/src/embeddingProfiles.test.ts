import { describe, expect, it } from 'vitest'

import { createEmbeddingProfileForm } from './embeddingProfiles'


describe('embedding profile defaults', () => {
  it('uses the server-side SiliconFlow BGE-M3 preset without a secret', () => {
    const form = createEmbeddingProfileForm()

    expect(form).toEqual({
      name: 'SiliconFlow BGE-M3',
      base_url: 'https://api.siliconflow.cn/v1',
      model: 'BAAI/bge-m3',
      dimension: 1024,
      credential_ref: 'siliconflow-embedding',
      provider: 'openai',
    })
    expect(JSON.stringify(form)).not.toMatch(/api.?key|bearer|secret/i)
  })

  it('returns a fresh form for each dialog reset', () => {
    const first = createEmbeddingProfileForm()
    first.model = 'changed'

    expect(createEmbeddingProfileForm().model).toBe('BAAI/bge-m3')
  })
})
