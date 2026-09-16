// Frontend-only "recent files" list for the Welcome screen. There is no
// backend concept of recent files (and none is needed) — this is persisted
// to `localStorage`, which in Electron's renderer is tied to the app's own
// user-data profile, so it survives app restarts.

export interface RecentFile {
  path: string
  filename: string
  openedAt: number
}

const STORAGE_KEY = 'pat.recentFiles'
const MAX_RECENT = 8

function loadInitial(): RecentFile[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export const recentFiles: RecentFile[] = $state(loadInitial())

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(recentFiles))
  } catch {
    // localStorage unavailable (private mode, etc.) — recent files simply
    // won't survive a restart; nothing else depends on this succeeding.
  }
}

/** Record (or bump) a file as recently opened, capped and deduplicated by path. */
export function recordRecentFile(path: string, filename: string): void {
  const existingIndex = recentFiles.findIndex((f) => f.path === path)
  if (existingIndex !== -1) recentFiles.splice(existingIndex, 1)
  recentFiles.unshift({ path, filename, openedAt: Date.now() })
  if (recentFiles.length > MAX_RECENT) recentFiles.length = MAX_RECENT
  persist()
}

/** Test-only: reset the list (and clear storage) so each test starts clean. */
export function resetForTesting(): void {
  recentFiles.length = 0
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}
