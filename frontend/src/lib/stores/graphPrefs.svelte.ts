// Graph display preferences. Frontend-only, persisted to localStorage (same
// pattern as theme.svelte.ts) — they change how the graph opens, not the
// analysis, so they don't belong in the backend's settings.

// How many seconds from the start of the recording the graph shows when a
// file (or channel) is loaded. The technician can zoom out from there.
export const DEFAULT_INITIAL_WINDOW_SECONDS = 5
export const MIN_INITIAL_WINDOW_SECONDS = 0.1
export const MAX_INITIAL_WINDOW_SECONDS = 3600

const STORAGE_KEY = 'pat.graph.initialWindowSeconds'

function isValidWindow(value: number): boolean {
  return Number.isFinite(value) && value >= MIN_INITIAL_WINDOW_SECONDS && value <= MAX_INITIAL_WINDOW_SECONDS
}

function loadInitial(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw !== null) {
      const value = Number(raw)
      if (isValidWindow(value)) return value
    }
  } catch {
    // ignore
  }
  return DEFAULT_INITIAL_WINDOW_SECONDS
}

export const graphPrefs: { initialWindowSeconds: number } = $state({ initialWindowSeconds: loadInitial() })

/** Returns false (and keeps the current value) when `seconds` is out of range. */
export function setInitialWindowSeconds(seconds: number): boolean {
  if (!isValidWindow(seconds)) return false
  graphPrefs.initialWindowSeconds = seconds
  try {
    localStorage.setItem(STORAGE_KEY, String(seconds))
  } catch {
    // localStorage unavailable — the choice just won't survive a restart.
  }
  return true
}

/** Test-only: reset to the default so each test starts from a clean state. */
export function resetForTesting(): void {
  graphPrefs.initialWindowSeconds = DEFAULT_INITIAL_WINDOW_SECONDS
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}
