// Stops a reviewer walking away from a beat they marked "uncertain" without
// deciding it.
//
// The mark is only ever saved together with a Confirm / Reject (it exists to
// grade the training data, and means nothing on a beat nobody judged), so
// ticking the box on an unreviewed beat leaves a PENDING mark here — in a store
// rather than in BeatCategoryPanel, which is remounted per beat and swapped
// between the side panel and the focus-mode overlay. Every way of leaving the
// beat asks `confirmLeave()` first; while a mark is pending that opens a popup
// (App.svelte renders it) and resolves with the reviewer's choice.
//
// In-memory only: a pending mark that was never saved is worth nothing after a
// reload.

interface PendingUncertain {
  path: string
  ts: number
}

export const reviewGuard: { pending: PendingUncertain | null; promptOpen: boolean } = $state({
  pending: null,
  promptOpen: false,
})

// The question currently on screen. Asking again while it is open (a second
// key press behind the popup) gets the same answer rather than a second popup.
let waiting: { promise: Promise<boolean>; resolve: (leave: boolean) => void } | null = null

/** Marks the beat at `ts` in `path` as uncertain, to be saved with the next decision. */
export function setPendingUncertain(path: string, ts: number): void {
  reviewGuard.pending = { path, ts }
}

/** Whether `ts` in `path` is the beat carrying the pending mark. */
export function isPendingUncertain(path: string, ts: number): boolean {
  return reviewGuard.pending?.path === path && reviewGuard.pending.ts === ts
}

export function hasPendingUncertain(): boolean {
  return reviewGuard.pending !== null
}

/**
 * Drops the pending mark: the decision was saved, the box unticked, the beat
 * deleted, or its screen closed. With `path` (and `ts`) only a mark on that
 * recording (beat) is dropped; a question still open about it is moot, so it
 * is answered "leave".
 */
export function clearPendingUncertain(path?: string, ts?: number): void {
  const pending = reviewGuard.pending
  if (!pending) return
  if (path !== undefined && pending.path !== path) return
  if (ts !== undefined && pending.ts !== ts) return
  reviewGuard.pending = null
  settle(true)
}

function settle(leave: boolean): void {
  const question = waiting
  waiting = null
  reviewGuard.promptOpen = false
  question?.resolve(leave)
}

/**
 * Resolves whether the reviewer may move away from the current beat: true at
 * once when no mark is pending, otherwise after they answer the popup.
 */
export function confirmLeave(): Promise<boolean> {
  if (!reviewGuard.pending) return Promise.resolve(true)
  if (waiting) return waiting.promise
  let resolve!: (leave: boolean) => void
  const promise = new Promise<boolean>((done) => {
    resolve = done
  })
  waiting = { promise, resolve }
  reviewGuard.promptOpen = true
  return promise
}

/** The popup's answer: leave (the pending mark is discarded) or go back to the beat. */
export function answerLeave(leave: boolean): void {
  if (leave) reviewGuard.pending = null
  settle(leave)
}

/** Test-only: reset so each test starts clean. */
export function resetForTesting(): void {
  reviewGuard.pending = null
  settle(false)
}
