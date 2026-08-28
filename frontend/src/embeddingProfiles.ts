export type EmbeddingProvider = 'openai' | 'tencent_multimodal'

export interface EmbeddingProfileForm {
  name: string
  base_url: string
  model: string
  dimension: number
  credential_ref: string
  provider: EmbeddingProvider
  api_key: string
  clear_api_key: boolean
}

export function createEmbeddingProfileForm(): EmbeddingProfileForm {
  return {
    name: 'SiliconFlow BGE-M3',
    base_url: 'https://api.siliconflow.cn/v1',
    model: 'BAAI/bge-m3',
    dimension: 1024,
    credential_ref: '',
    provider: 'openai',
    api_key: '',
    clear_api_key: false,
  }
}

export function buildEmbeddingProfilePayload(
  form: EmbeddingProfileForm,
  profileId?: string,
) {
  const common = {
    name: form.name,
    base_url: form.base_url,
    model: form.model,
    dimension: form.dimension,
    provider: form.provider,
    api_key: form.api_key,
    clear_api_key: form.clear_api_key,
  }
  return profileId ? common : { ...common, credential_ref: form.credential_ref }
}

export function buildEmbeddingProbePayload(
  form: EmbeddingProfileForm,
  profileId?: string,
) {
  return {
    base_url: form.base_url,
    model: form.model,
    provider: form.provider,
    credential_ref: profileId ? '' : form.credential_ref,
    api_key: form.api_key,
    ...(profileId ? { profile_id: profileId } : {}),
  }
}
