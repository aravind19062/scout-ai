export function timeAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return new Date(iso).toLocaleDateString()
}

export const fmtInt = (n?: number) => (n ?? 0).toLocaleString()
export const fmtUsd = (n?: number) => `$${(n ?? 0).toFixed((n ?? 0) < 1 ? 4 : 2)}`
export const fmtDuration = (s?: number) => (!s ? '—' : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`)
