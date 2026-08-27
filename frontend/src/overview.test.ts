import { describe, expect, it } from 'vitest'

import { currentFailedJobs, latestJobsByRepository } from './overview'
import type { IndexJob } from './types'

function job(
  repository_id: string,
  status: IndexJob['status'],
  created_at: string,
): IndexJob {
  return {
    id: `${repository_id}-${created_at}`,
    repository_id,
    status,
    progress: status === 'succeeded' ? 100 : 45,
    message: '',
    error: status === 'failed' ? 'failed' : '',
    commit: 'a'.repeat(40),
    created_at,
    started_at: created_at,
    finished_at: created_at,
  }
}

describe('overview job health', () => {
  it('counts only repositories whose latest run failed', () => {
    expect(
      currentFailedJobs([
        job('repo-a', 'failed', '2026-08-27T01:00:00Z'),
        job('repo-a', 'succeeded', '2026-08-27T02:00:00Z'),
        job('repo-b', 'succeeded', '2026-08-27T01:00:00Z'),
        job('repo-b', 'failed', '2026-08-27T03:00:00Z'),
      ]).map((item) => item.repository_id),
    ).toEqual(['repo-b'])
  })

  it('shows only the latest run for each repository', () => {
    expect(
      latestJobsByRepository([
        job('repo-a', 'failed', '2026-08-27T01:00:00Z'),
        job('repo-a', 'succeeded', '2026-08-27T02:00:00Z'),
        job('repo-b', 'failed', '2026-08-27T03:00:00Z'),
      ]).map((item) => [item.repository_id, item.status]),
    ).toEqual([
      ['repo-b', 'failed'],
      ['repo-a', 'succeeded'],
    ])
  })
})
