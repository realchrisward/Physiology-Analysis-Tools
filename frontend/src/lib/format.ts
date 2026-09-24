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
