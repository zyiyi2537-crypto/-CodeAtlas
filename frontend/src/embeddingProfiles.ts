export type EmbeddingProvider = 'openai' | 'tencent_multimodal'

export interface EmbeddingProfileForm {
  name: string
  base_url: string
  model: string
  dimension: number
  credential_ref: string
  provider: EmbeddingProvider
}

export function createEmbeddingProfileForm(): EmbeddingProfileForm {
  return {
    name: 'SiliconFlow BGE-M3',
    base_url: 'https://api.siliconflow.cn/v1',
    model: 'BAAI/bge-m3',
    dimension: 1024,
    credential_ref: 'siliconflow-embedding',
    provider: 'openai',
  }
}
