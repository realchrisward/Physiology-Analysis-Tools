// Shared, cross-component registry of every file imported this session.
//
// Svelte 5 runes-in-module pattern: a `$state` array declared here is
// reactive for every component that imports it, with no prop drilling.
// importQueue.svelte.ts is the only thing that pushes into this array, and
// it never adds a second row for a path already listed — every list of
// these rows is keyed by path, and Svelte throws on a duplicate key.

import { evictFiles } from '../api/files'

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
  // Loaded only because an FP/FN review project needed it (see
  // flagProjects.svelte.ts). Such a row leaves with its project; a file the
  // technician imported themselves stays even when a project also uses it.
  fromProject?: boolean
}

export const fileRegistry: FileRow[] = $state([])

// The identity ReviewWorkspace needs to open a file — shared between
// importQueue.svelte.ts (which produces it: the first file of an import, or
// a recent-file re-open) and App.svelte (which consumes it to navigate).
export interface ReviewSelection {
  path: string
  channels: string[]
  defaultChannel: string
  // Set when the file was opened from an FP/FN review project (see
  // flagProjects.svelte.ts) — the breadcrumb shows the project, and the file
  // is not added to Recent files.
  projectId?: string | null
}

// Set by importQueue.svelte.ts whenever an explicit, user-initiated import
// action (Import Files/Folder, or opening a Recent file) has a file ready
// to show — App.svelte watches this and navigates straight to it, then
// clears it back to `null`. A plain nullable field (not a callback) so the
// store has no dependency on App.svelte at all.
export const autoOpenRequest: { value: ReviewSelection | null } = $state({ value: null })

// Bumped by App.svelte every time a file is shown for review. A slow import
// started earlier (an FP/FN project loading dozens of recordings) compares it
// before and after, and doesn't pull the technician away from a file they
// opened in the meantime.
export const reviewNavigation: { seq: number } = $state({ seq: 0 })

// Drops the backend's in-memory copies (signal, beats, graph windows) of
// files that left the session — without this a long session, or a project of
// dozens of multi-hour recordings, keeps every one of them in RAM. Saved
// review work in SQLite is untouched. Fire-and-forget: a failure only costs
// memory, and `apiPost` never throws.
function releaseBackendCopies(paths: string[]): void {
  if (paths.length > 0) void evictFiles(paths)
}

/**
 * Empties this session's file list — the sidebar's "Clear files" action.
 *
 * Nothing on disk and no persisted review state is touched; the backend only
 * frees its in-memory copies. Re-importing a cleared file brings it back with
 * its review history intact, exactly as reopening it in a new session would.
 */
export function clearFileRegistry(): void {
  const paths = fileRegistry.map((row) => row.path)
  for (const row of fileRegistry) if (row.status === 'queued') row.status = 'ready'
  fileRegistry.length = 0
  autoOpenRequest.value = null
  releaseBackendCopies(paths)
}

/**
 * Takes files out of this session's list — the sidebar's per-file X, or an
 * FP/FN project closing. Like `clearFileRegistry`: nothing on disk or in
 * saved review work is touched, and re-importing brings a file back with its
 * history. A file still waiting for detection is put back to 'ready' so the
 * detection worker skips it instead of processing a file nobody can see any
 * more. Returns the paths that were actually listed.
 */
export function removeFiles(paths: string[]): string[] {
  const removed: string[] = []
  for (const path of new Set(paths)) {
    const index = fileRegistry.findIndex((row) => row.path === path)
    if (index === -1) continue
    if (fileRegistry[index].status === 'queued') fileRegistry[index].status = 'ready'
    fileRegistry.splice(index, 1)
    removed.push(path)
  }
  releaseBackendCopies(removed)
  return removed
}

export function removeFile(path: string): boolean {
  return removeFiles([path]).length > 0
}

/** Test-only: reset the registry so each test starts from a clean state. */
export function resetForTesting(): void {
  fileRegistry.length = 0
  autoOpenRequest.value = null
  reviewNavigation.seq = 0
}
