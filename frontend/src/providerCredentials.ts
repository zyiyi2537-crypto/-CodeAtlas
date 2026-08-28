import type { LlmModel, LlmProvider } from './types'

export interface LlmProviderForm {
  name: string
  base_url: string
  api_key: string
  model: string
  models: LlmModel[]
  clear_api_key: boolean
}

export function createLlmProviderForm(
  provider?: Pick<LlmProvider, 'name' | 'base_url' | 'model' | 'models'>,
): LlmProviderForm {
  return {
    name: provider?.name ?? '',
    base_url: provider?.base_url ?? '',
    api_key: '',
    model: provider?.model ?? '',
    models: provider?.models ? [...provider.models] : [],
    clear_api_key: false,
  }
}

export function buildLlmProviderPayload(form: LlmProviderForm) {
  return {
    name: form.name,
    base_url: form.base_url,
    model: form.model,
    models: form.models,
    api_key: form.api_key,
    clear_api_key: form.clear_api_key,
  }
}

export function buildLlmSyncPayload(form: LlmProviderForm, providerId?: string) {
  return {
    base_url: form.base_url,
    api_key: form.api_key,
    ...(providerId ? { provider_id: providerId } : {}),
  }
}
