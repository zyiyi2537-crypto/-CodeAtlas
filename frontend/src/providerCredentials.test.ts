import { describe, expect, it } from 'vitest'

import {
  buildLlmProviderPayload,
  buildLlmSyncPayload,
  createLlmProviderForm,
} from './providerCredentials'


describe('LLM provider credential forms', () => {
  it('keeps the saved key when an edited provider submits a blank key', () => {
    const form = createLlmProviderForm({
      name: 'Kimi',
      base_url: 'https://api.kimi.com/coding/v1',
      model: 'kimi-for-coding',
      models: [],
    })

    expect(buildLlmProviderPayload(form)).toEqual({
      name: 'Kimi',
      base_url: 'https://api.kimi.com/coding/v1',
      model: 'kimi-for-coding',
      models: [],
      api_key: '',
      clear_api_key: false,
    })
  })

  it('requires an explicit clear action instead of treating blank as clear', () => {
    const form = createLlmProviderForm()
    form.clear_api_key = true

    expect(buildLlmProviderPayload(form).clear_api_key).toBe(true)
  })

  it('lets an edit test use the saved key without exposing it', () => {
    const form = createLlmProviderForm({
      name: 'Kimi',
      base_url: 'https://api.kimi.com/coding/v1',
      model: 'kimi-for-coding',
      models: [],
    })

    expect(buildLlmSyncPayload(form, 'provider-1')).toEqual({
      base_url: 'https://api.kimi.com/coding/v1',
      api_key: '',
      provider_id: 'provider-1',
    })
  })
})