export type ExternalSourceProvider = 'aws_s3' | 'tencent_cos' | 'notion' | 'confluence'

export interface ExternalSourceForm {
  name: string
  provider: ExternalSourceProvider
  collection_id: string
  credential_ref: string
  poll_interval_seconds: number
  bucket: string
  prefix: string
  region: string
  endpoint_url: string
  base_url: string
  space_key: string
  root_page_id: string
  deployment: string
}

export const externalSourceProviders = [
  { value: 'aws_s3' as const, label: 'AWS S3', phase: 1 },
  { value: 'tencent_cos' as const, label: '腾讯云 COS', phase: 1 },
  { value: 'notion' as const, label: 'Notion', phase: 2 },
  { value: 'confluence' as const, label: 'Confluence', phase: 2 },
]

function compact(values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value.trim()))
}

export function buildExternalSourcePayload(form: ExternalSourceForm) {
  let config: Record<string, string>
  if (form.provider === 'aws_s3') {
    config = compact({
      bucket: form.bucket,
      prefix: form.prefix,
      region: form.region,
      endpoint_url: form.endpoint_url,
    })
  } else if (form.provider === 'tencent_cos') {
    config = compact({ bucket: form.bucket, prefix: form.prefix, region: form.region })
  } else if (form.provider === 'notion') {
    config = compact({ root_page_id: form.root_page_id })
  } else {
    config = compact({
      base_url: form.base_url,
      space_key: form.space_key,
      root_page_id: form.root_page_id,
      deployment: form.deployment,
    })
  }
  return {
    name: form.name.trim(),
    provider: form.provider,
    collection_id: form.collection_id,
    credential_ref: form.credential_ref.trim(),
    poll_interval_seconds: form.poll_interval_seconds,
    enabled: true,
    config,
  }
}
