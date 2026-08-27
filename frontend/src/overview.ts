import type { IndexJob } from './types'

export function latestJobsByRepository(jobs: IndexJob[]): IndexJob[] {
  const latest = new Map<string, IndexJob>()
  for (const job of [...jobs].sort((left, right) =>
    right.created_at.localeCompare(left.created_at),
  )) {
    if (!latest.has(job.repository_id)) latest.set(job.repository_id, job)
  }
  return [...latest.values()]
}

export function currentFailedJobs(jobs: IndexJob[]): IndexJob[] {
  return latestJobsByRepository(jobs).filter((job) => job.status === 'failed')
}
