// Saved-review counts per recording (POST /files/summaries), for sorting and
// filtering the sidebar and Home lists by beats, review progress and so on.
//
// Fetched only when a list asks (its Sort & filter panel is open, or its sort
// or filters need them) and only for paths not known yet. A saved review
// change (reviewChanges.ts) marks that path stale and refetches it shortly
// after, so a list sorted by "left to review" follows the review.

import { getFileSummaries } from '../api/persistence'
import type { FileSummary } from '../api/types'
import { onReviewChanged } from '../reviewChanges'

export const fileSummaries: Record<string, FileSummary> = $state({})

// How many fetches are running: the panel says "Counting…" meanwhile.
export const summaryLoading: { count: number } = $state({ count: 0 })

const stale = new Set<string>()
const inFlight = new Set<string>()
let refreshTimer: ReturnType<typeof setTimeout> | null = null
// Bumped by a reset, so a fetch started before it lands nowhere.
let generation = 0

const REFRESH_DELAY_MS = 800

/** Fetches what is not known yet (or went stale) of `paths`; the rest is left as it is. */
export async function loadSummaries(paths: string[]): Promise<void> {
  const wanted = [...new Set(paths)].filter((path) => (!(path in fileSummaries) || stale.has(path)) && !inFlight.has(path))
  if (wanted.length === 0) return
  for (const path of wanted) {
    inFlight.add(path)
    stale.delete(path)
  }
  const startedIn = generation
  summaryLoading.count += 1
  try {
    const result = await getFileSummaries(wanted)
    if (startedIn !== generation) return
    if (result.status === 'ok') {
      for (const summary of result.summaries ?? []) fileSummaries[summary.path] = summary
    }
  } finally {
    if (startedIn === generation) {
      summaryLoading.count = Math.max(0, summaryLoading.count - 1)
      for (const path of wanted) inFlight.delete(path)
      // Changed again while this was on its way: its answer is already old.
      if (wanted.some((path) => stale.has(path))) scheduleRefresh()
    }
  }
}

function scheduleRefresh(): void {
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    void loadSummaries([...stale])
  }, REFRESH_DELAY_MS)
}

// Only paths a list has asked about are refetched; the rest are fetched
// fresh whenever a list first needs them.
onReviewChanged((paths) => {
  let any = false
  for (const path of paths) {
    if (path in fileSummaries || inFlight.has(path)) {
      stale.add(path)
      any = true
    }
  }
  if (any) scheduleRefresh()
})

/** Test-only: forget everything, so each test starts clean. */
export function resetForTesting(): void {
  generation += 1
  for (const path of Object.keys(fileSummaries)) delete fileSummaries[path]
  stale.clear()
  inFlight.clear()
  summaryLoading.count = 0
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = null
}
