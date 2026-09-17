// Shared import + auto-run-detection orchestration — the exact detection
// logic that used to live inside ImportScreen.svelte, unchanged, lifted here
// so both the Welcome screen's full controls and the Sidebar's compact
// "+ Import" affordance drive the SAME underlying registry.
//
// Importing and detecting are DELIBERATELY decoupled into two independent
// state flags (`importing` / `detecting`) rather than one shared "busy"
// state: a technician reviewing one file must be able to import more files
// at any moment, even while an earlier batch's auto-run detection queue is
// still chewing through previously imported files in the background. Only
// the picker dialog + the `POST /files/import` round trip itself briefly
// disable the Import buttons (to prevent a double-submit race); newly
// imported files are simply appended to the detection queue, which a single
// worker loop drains one file at a time (unchanged sequential-detection
// behavior, just no longer gating new imports).

import { detectBeats } from '../api/beats'
import { importFiles } from '../api/files'
import { estimate, recordSample } from './eta'
import { autoOpenRequest, fileRegistry, type FileRow } from './fileRegistry.svelte'
import { recordRecentFile } from './recentFiles.svelte'

export const importQueue: { importing: boolean; detecting: boolean; error: string; autoRun: boolean } = $state({
  // True only while the OS picker is open or the `POST /files/import` call
  // itself is in flight — the Import Files/Import Folder buttons are
  // disabled only for this brief window, never while detection is running.
  importing: false,
  // True while the detection worker loop below is actively processing the
  // queue. Drives the Stop button's visibility/enablement.
  detecting: false,
  error: '',
  autoRun: true,
})

// FIFO of rows awaiting detection. A single worker loop (started by
// `enqueueDetection` if not already running) drains this — so a second
// import that arrives mid-batch just extends the same queue instead of
// starting a competing detection run.
const pendingDetection: FileRow[] = []
let workerRunning = false
let stopRequested = false

async function importPaths(paths: string[]): Promise<void> {
  if (paths.length === 0) return

  importQueue.error = ''
  const result = await importFiles(paths)

  if ('error' in result) {
    importQueue.error = result.error
    return
  }

  const newRows: FileRow[] = []
  // The first successfully-imported file with a channel gets opened
  // automatically once this call finishes — see `autoOpenRequest`'s own
  // comment. Only the first: an "Import Folder" of many files still opens
  // just one, the rest simply appear in the sidebar as usual.
  let autoOpenCandidate: FileRow | null = null
  for (const fileResult of result.results) {
    fileRegistry.push({
      path: fileResult.path,
      filename: fileResult.filename,
      status: fileResult.status === 'ok' ? 'ready' : 'error',
      channels: fileResult.channels,
      defaultChannel: fileResult.default_channel,
      size: fileResult.size,
      error: fileResult.error,
      beatCount: null,
      meanHr: null,
    })
    // Capture the reference back out of the reactive registry (rather than
    // holding onto the plain object literal above) so mutations made later
    // in the detection queue are tracked by Svelte's state proxy.
    const row = fileRegistry[fileRegistry.length - 1]
    newRows.push(row)

    if (fileResult.status === 'ok') {
      recordRecentFile(fileResult.path, fileResult.filename)
      if (!autoOpenCandidate && row.defaultChannel !== null) {
        autoOpenCandidate = row
      }
    }
  }

  if (autoOpenCandidate) {
    autoOpenRequest.value = {
      path: autoOpenCandidate.path,
      channels: autoOpenCandidate.channels,
      defaultChannel: autoOpenCandidate.defaultChannel!,
    }
  }

  if (importQueue.autoRun) {
    enqueueDetection(newRows)
  }
}

function enqueueDetection(rows: FileRow[]): void {
  pendingDetection.push(...rows)
  if (!workerRunning) {
    void runDetectionWorker()
  }
}

async function runDetectionWorker(): Promise<void> {
  workerRunning = true
  importQueue.detecting = true
  stopRequested = false
  try {
    while (pendingDetection.length > 0) {
      if (stopRequested) {
        // Lets the in-flight file finish (this check only runs between
        // files), then drops everything else still queued — no partial
        // results are ever kept for a dropped file.
        pendingDetection.length = 0
        break
      }

      const row = pendingDetection.shift()!
      if (row.status !== 'ready' || row.defaultChannel === null) continue

      row.status = 'detecting'
      const result = await detectBeats(row.path, row.defaultChannel)

      if (result.status === 'ok' && !result.error) {
        if (row.size !== null) {
          recordSample(row.size, result.elapsed_seconds)
        }
        row.status = 'detected'
        row.beatCount = result.count
        row.meanHr = result.mean_hr
      } else {
        row.status = 'detection-error'
        row.error = result.error
      }
    }
  } finally {
    workerRunning = false
    importQueue.detecting = false
    stopRequested = false
  }
}

export function handleStop(): void {
  if (importQueue.detecting) {
    stopRequested = true
  }
}

export function formatEta(size: number | null): string {
  if (size === null) return 'Calculating…'
  const seconds = estimate(size)
  if (seconds === null) return 'Calculating…'
  return `~${Math.max(1, Math.round(seconds))}s`
}

export async function handleImportFiles(): Promise<void> {
  // Set synchronously, before awaiting the picker, so the buttons are
  // disabled from the very first click — covering the picker-open window
  // and the import round trip, nothing more.
  importQueue.importing = true
  try {
    const paths = (await window.api?.pickFiles()) ?? []
    await importPaths(paths)
  } finally {
    importQueue.importing = false
  }
}

export async function handleImportFolder(): Promise<void> {
  importQueue.importing = true
  try {
    const paths = (await window.api?.pickFolder()) ?? []
    await importPaths(paths)
  } finally {
    importQueue.importing = false
  }
}

/**
 * Opens a file from Recent Files (Welcome screen or the Sidebar's own
 * collapsible Recent Files section), skipping the OS picker.
 *
 * If the file is already in this session's `fileRegistry` — i.e. it was
 * imported earlier in this same session, whether from a fresh import or a
 * prior recent-file open — this just re-opens it directly: no re-import,
 * no re-run of beat detection. Previously every recent-file click went
 * through the full import+auto-detect flow unconditionally, silently
 * re-running detection (and discarding the in-memory ETA-sample benefit of
 * already having the file's data cached) every single time, even for a
 * file already open moments ago. Only a file NOT in the registry — e.g.
 * after an app restart, when `fileRegistry` is empty but `recentFiles`
 * (localStorage-backed) still remembers it — goes through the real import.
 */
export async function openRecentFile(path: string): Promise<void> {
  const existing = fileRegistry.find((row) => row.path === path)
  if (existing && existing.status !== 'error' && existing.defaultChannel !== null) {
    recordRecentFile(existing.path, existing.filename)
    autoOpenRequest.value = {
      path: existing.path,
      channels: existing.channels,
      defaultChannel: existing.defaultChannel,
    }
    return
  }

  importQueue.importing = true
  try {
    await importPaths([path])
  } finally {
    importQueue.importing = false
  }
}

/** Test-only: reset queue state so each test starts from a clean state. */
export function resetForTesting(): void {
  importQueue.importing = false
  importQueue.detecting = false
  importQueue.error = ''
  importQueue.autoRun = true
  pendingDetection.length = 0
  workerRunning = false
  stopRequested = false
}
