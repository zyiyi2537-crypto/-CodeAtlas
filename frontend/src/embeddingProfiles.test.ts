import { describe, expect, it } from 'vitest'

import {
  buildEmbeddingProfilePayload,
  buildEmbeddingProbePayload,
  createEmbeddingProfileForm,
} from './embeddingProfiles'


describe('embedding profile defaults', () => {
  it('uses the SiliconFlow BGE-M3 preset with a blank write-only key', () => {
    const form = createEmbeddingProfileForm()

    expect(form).toEqual({
      name: 'SiliconFlow BGE-M3',
      base_url: 'https://api.siliconflow.cn/v1',
      model: 'BAAI/bge-m3',
      dimension: 1024,
      credential_ref: '',
      provider: 'openai',
      api_key: '',
      clear_api_key: false,
    })
    expect(form.api_key).toBe('')
  })

  it('returns a fresh form for each dialog reset', () => {
    const first = createEmbeddingProfileForm()
    first.model = 'changed'

    expect(createEmbeddingProfileForm().model).toBe('BAAI/bge-m3')
  })

  it('keeps an existing key when the edit field is blank', () => {
    const form = createEmbeddingProfileForm()
    form.name = 'Existing BGE'

    expect(buildEmbeddingProfilePayload(form, 'profile-1')).toEqual({
      name: 'Existing BGE',
      base_url: 'https://api.siliconflow.cn/v1',
      model: 'BAAI/bge-m3',
      dimension: 1024,
      provider: 'openai',
      api_key: '',
      clear_api_key: false,
    })
  })

  it('uses a saved credential when probing an existing profile', () => {
    const form = createEmbeddingProfileForm()

    expect(buildEmbeddingProbePayload(form, 'profile-1')).toEqual({
      base_url: 'https://api.siliconflow.cn/v1',
      model: 'BAAI/bge-m3',
      provider: 'openai',
      credential_ref: '',
      api_key: '',
      profile_id: 'profile-1',
    })
  })
})
