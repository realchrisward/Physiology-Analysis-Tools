// Frontend-only "recent files" list for the Welcome screen. There is no
// backend concept of recent files (and none is needed) — this is persisted
// to `localStorage`, which in Electron's renderer is tied to the app's own
// user-data profile, so it survives app restarts.

export interface RecentFile {
  path: string
  filename: string
  openedAt: number
  // The recording's channel names, so Home can filter by channel without
  // loading it. Missing for entries saved before this was remembered.
  channels?: string[]
}

const STORAGE_KEY = 'pat.recentFiles'
const MAX_RECENT = 30

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

/** Record (or bump) a file as recently opened, capped and deduplicated by path.
 * `channels` (when known) is remembered with it; left out keeps what was. */
export function recordRecentFile(path: string, filename: string, channels?: string[]): void {
  const existingIndex = recentFiles.findIndex((f) => f.path === path)
  const known = channels && channels.length > 0 ? [...channels] : recentFiles[existingIndex]?.channels
  if (existingIndex !== -1) recentFiles.splice(existingIndex, 1)
  recentFiles.unshift({ path, filename, openedAt: Date.now(), ...(known ? { channels: known } : {}) })
  if (recentFiles.length > MAX_RECENT) recentFiles.length = MAX_RECENT
  persist()
}

/** Drops just the given paths from the recent-files list (the Home screen's
 * "Clear selected"), persisting the result. Paths not present are ignored. */
export function removeRecentFiles(paths: string[]): void {
  if (paths.length === 0) return
  const toRemove = new Set(paths)
  for (let i = recentFiles.length - 1; i >= 0; i--) {
    if (toRemove.has(recentFiles[i].path)) recentFiles.splice(i, 1)
  }
  persist()
}

/** Empties the whole recent-files list (the Home screen's "Clear all"). */
export function clearRecentFiles(): void {
  recentFiles.length = 0
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
