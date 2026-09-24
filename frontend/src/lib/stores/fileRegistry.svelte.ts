// Shared, cross-component registry of every file imported this session.
//
// Svelte 5 runes-in-module pattern: a `$state` array declared here is
// reactive for every component that imports it, with no prop drilling.
// `ImportScreen` is still the only thing that ever pushes into or mutates
// this array (its import/detect orchestration is unchanged); `Sidebar` is a
// pure read-plus-click consumer of the same live objects.

export interface FileRow {
  path: string
  filename: string
  // 'ready' - imported, not yet queued for detection (or auto-run is off).
  // 'queued' - waiting in the detection worker's FIFO, not yet started.
  // 'detecting' - the worker is actively running detection for this file.
  // 'detected' / 'detection-error' - finished, successfully or not.
  // 'error' - the import itself failed; never queued at all.
  status: 'ready' | 'error' | 'queued' | 'detecting' | 'detected' | 'detection-error'
  channels: string[]
  defaultChannel: string | null
  size: number | null
  error: string | null
  beatCount: number | null
  meanHr: number | null
}

export const fileRegistry: FileRow[] = $state([])

// The identity ReviewWorkspace needs to open a file — shared between
// importQueue.svelte.ts (which produces it: the first file of an import, or
// a recent-file re-open) and App.svelte (which consumes it to navigate).
export interface ReviewSelection {
  path: string
  channels: string[]
  defaultChannel: string
}

// Set by importQueue.svelte.ts whenever an explicit, user-initiated import
// action (Import Files/Folder, or opening a Recent file) has a file ready
// to show — App.svelte watches this and navigates straight to it, then
// clears it back to `null`. A plain nullable field (not a callback) so the
// store has no dependency on App.svelte at all.
export const autoOpenRequest: { value: ReviewSelection | null } = $state({ value: null })

/**
 * Empties this session's file list — the sidebar's "Clear files" action.
 *
 * Frontend-only by design: it clears what the technician is looking at, not
 * anything on disk, not the backend's caches, and not any persisted review
 * state. Re-importing a cleared file brings it back with its review history
 * intact, exactly as reopening it in a new session would.
 */
export function clearFileRegistry(): void {
  fileRegistry.length = 0
  autoOpenRequest.value = null
}

/**
 * Takes one file out of this session's list — the sidebar's per-file X.
 * Like `clearFileRegistry`, frontend-only: nothing on disk, in the backend
 * caches or in saved review work is touched, and re-importing brings the
 * file back with its history. A file still waiting for detection is put back
 * to 'ready' so the detection worker skips it instead of processing a file
 * nobody can see any more.
 */
export function removeFile(path: string): boolean {
  const index = fileRegistry.findIndex((row) => row.path === path)
  if (index === -1) return false
  if (fileRegistry[index].status === 'queued') fileRegistry[index].status = 'ready'
  fileRegistry.splice(index, 1)
  return true
}

/** Test-only: reset the registry so each test starts from a clean state. */
export function resetForTesting(): void {
  fileRegistry.length = 0
  autoOpenRequest.value = null
}
