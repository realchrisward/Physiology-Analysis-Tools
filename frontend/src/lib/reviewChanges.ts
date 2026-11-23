// A signal that a recording's saved review just changed (a decision, a beat
// added or removed, detection saved, a report applied...). The API calls that
// save review work send it once their request is done; the file lists' saved
// counts (fileSummaries.svelte.ts) listen, so a list sorted by "left to
// review" stays true. Kept free of imports so the API layer can use it.

type Listener = (paths: string[]) => void

const listeners = new Set<Listener>()

export function onReviewChanged(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function noteReviewChanged(...paths: string[]): void {
  if (paths.length === 0) return
  for (const listener of listeners) listener(paths)
}

/**
 * Tells the listeners about `paths` once `request` has finished, and hands
 * back `request` itself: the caller's own await sees no extra step.
 */
export function afterReviewChange<T>(request: Promise<T>, ...paths: string[]): Promise<T> {
  void request.then(
    () => noteReviewChanged(...paths),
    () => {},
  )
  return request
}
