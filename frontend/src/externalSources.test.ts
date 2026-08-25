import { describe, expect, it } from 'vitest'

import { buildExternalSourcePayload, externalSourceProviders } from './externalSources'


describe('external source configuration', () => {
  it('builds an AWS S3 payload without secret fields', () => {
    const payload = buildExternalSourcePayload({
      name: 'AWS manuals',
      provider: 'aws_s3',
      collection_id: 'collection-1',
      credential_ref: 'aws-docs',
      poll_interval_seconds: 1800,
      bucket: 'company-docs',
      prefix: 'manuals/',
      region: 'ap-southeast-1',
      endpoint_url: '',
      base_url: '',
      space_key: '',
      root_page_id: '',
      deployment: 'cloud',
    })

    expect(payload).toEqual({
      name: 'AWS manuals',
      provider: 'aws_s3',
      collection_id: 'collection-1',
      credential_ref: 'aws-docs',
      poll_interval_seconds: 1800,
      enabled: true,
      config: {
        bucket: 'company-docs',
        prefix: 'manuals/',
        region: 'ap-southeast-1',
      },
    })
    expect(JSON.stringify(payload)).not.toMatch(/access.key|secret|token/i)
  })

  it('declares all four phased providers', () => {
    expect(externalSourceProviders.map((provider) => provider.value)).toEqual([
      'aws_s3',
      'tencent_cos',
      'notion',
      'confluence',
    ])
  })
})
