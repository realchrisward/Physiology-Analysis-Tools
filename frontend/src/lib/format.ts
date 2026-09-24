/**
 * "24 Sep 2026, 14:32" in the viewer's locale and time zone, for an ISO
 * timestamp from the backend. An unreadable value is shown as it came rather
 * than as "Invalid Date".
 */
export function formatEditTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function startOfDay(ms: number): number {
  const date = new Date(ms)
  date.setHours(0, 0, 0, 0)
  return date.getTime()
}

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? '' : 's'} ago`
}

/**
 * "just now", "5 minutes ago", "3 hours ago", "yesterday at 14:32",
 * "3 days ago", or a plain date once it is a week or more old. Days are
 * calendar days, so 23:50 last night is "yesterday" at 00:10 rather than
 * "0 hours ago".
 */
export function formatRelativeTime(ts: number, now: number = Date.now()): string {
  const diffMs = now - ts
  if (!Number.isFinite(ts) || diffMs < 0) return 'just now'

  const minutes = Math.floor(diffMs / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return plural(minutes, 'minute')

  const dayDiff = Math.round((startOfDay(now) - startOfDay(ts)) / 86400000)
  if (dayDiff === 0) return plural(Math.floor(minutes / 60), 'hour')

  const date = new Date(ts)
  if (dayDiff === 1) {
    const time = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    return `yesterday at ${time}`
  }
  if (dayDiff < 7) return plural(dayDiff, 'day')
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}
