// Asks App.svelte to remount the review screen of one recording, so it reads
// the saved review again from scratch (after a report replaced it, or a backup
// was restored). The screen seeds most of its state once on mount, so a
// remount is the one reliable way to show a review that changed underneath it.

export const reviewReload: { seq: Record<string, number> } = $state({ seq: {} })

export function reloadReview(path: string): void {
  reviewReload.seq[path] = (reviewReload.seq[path] ?? 0) + 1
}

/** Test-only: reset so each test starts clean. */
export function resetForTesting(): void {
  for (const key of Object.keys(reviewReload.seq)) delete reviewReload.seq[key]
}
