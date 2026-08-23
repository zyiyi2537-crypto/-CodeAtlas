export function shortCommit(commit: string): string {
  return commit ? commit.slice(0, 8) : '—'
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(new Date(value))
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}
