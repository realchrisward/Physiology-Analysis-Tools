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
import { autoOpenRequest, fileRegistry, removeFiles, type FileRow } from './fileRegistry.svelte'
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

// Paths whose `POST /files/import` is in flight. A second request for the
// same file (a double-click, or a project needing a file another import is
// already loading) waits for that one instead of listing the file twice.
const inFlight = new Map<string, Promise<unknown>>()
// Import Files/Folder, Recent files and FP/FN projects can overlap, so
// `importing` is derived from a count of running imports — a single flag
// would be cleared by whichever finished first while another still ran.
let activeImports = 0

function beginImport(): void {
  activeImports += 1
  importQueue.importing = true
}

function endImport(): void {
  activeImports = Math.max(0, activeImports - 1)
  importQueue.importing = activeImports > 0
}

interface ImportOptions {
  // Show the first requested file that can be opened (see `autoOpenRequest`).
  autoOpen?: boolean
  recordRecent?: boolean
  // Loaded for an FP/FN review project rather than by the technician.
  fromProject?: boolean
}

/**
 * Adds files to this session and queues them for detection. A path already
 * listed is never imported again — every list of registry rows is keyed by
 * path and Svelte throws on a duplicate key — except a row whose import
 * failed, which is retried (the drive may be back, the folder restored).
 * Returns the paths this call added.
 */
async function importPaths(
  paths: string[],
  { autoOpen = true, recordRecent = true, fromProject = false }: ImportOptions = {},
): Promise<string[]> {
  const requested = [...new Set(paths)]
  if (requested.length === 0) return []

  const failed = requested.filter((path) => fileRegistry.some((row) => row.path === path && row.status === 'error'))
  if (failed.length > 0) removeFiles(failed)

  const waiting = requested.filter((path) => inFlight.has(path)).map((path) => inFlight.get(path)!)
  const fresh = requested.filter((path) => !inFlight.has(path) && !fileRegistry.some((row) => row.path === path))

  importQueue.error = ''
  const newRows: FileRow[] = []
  if (fresh.length > 0) {
    const request = importFiles(fresh)
    for (const path of fresh) inFlight.set(path, request)
    let result: Awaited<typeof request>
    try {
      result = await request
    } finally {
      for (const path of fresh) if (inFlight.get(path) === request) inFlight.delete(path)
    }

    if ('error' in result) {
      importQueue.error = result.error
    } else {
      for (const fileResult of result.results) {
        if (fileRegistry.some((row) => row.path === fileResult.path)) continue
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
          fromProject,
        })
        // Capture the reference back out of the reactive registry (rather than
        // holding onto the plain object literal above) so mutations made later
        // in the detection queue are tracked by Svelte's state proxy.
        newRows.push(fileRegistry[fileRegistry.length - 1])
        if (fileResult.status === 'ok' && recordRecent) recordRecentFile(fileResult.path, fileResult.filename)
      }
    }
  }
  if (waiting.length > 0) await Promise.allSettled(waiting)

  // A file the technician imported themselves is theirs from now on, even if
  // an FP/FN project loaded it first — closing the project must not take it.
  if (!fromProject) {
    for (const path of requested) {
      const row = fileRegistry.find((candidate) => candidate.path === path)
      if (row?.fromProject) row.fromProject = false
    }
  }

  // The first requested file that can be shown opens automatically — see
  // `autoOpenRequest`'s own comment. Only the first: an "Import Folder" of
  // many files still opens just one, the rest simply appear in the sidebar.
  if (autoOpen) {
    for (const path of requested) {
      const row = fileRegistry.find((candidate) => candidate.path === path)
      if (row && row.status !== 'error' && row.defaultChannel !== null) {
        autoOpenRequest.value = { path: row.path, channels: row.channels, defaultChannel: row.defaultChannel }
        break
      }
    }
  }

  if (importQueue.autoRun) {
    enqueueDetection(newRows)
  }
  return newRows.map((row) => row.path)
}

function enqueueDetection(rows: FileRow[]): void {
  // Marked 'queued' up front (not left at 'ready') so the sidebar — and a
  // technician who opens the file before the worker reaches it — can tell
  // "will be detected shortly" apart from "not queued at all". Only rows
  // the worker will actually pick up get this — a row whose import itself
  // failed (no `defaultChannel`) would otherwise show a misleading
  // "queued" it can never leave.
  for (const row of rows) {
    if (row.status === 'ready' && row.defaultChannel !== null) {
      row.status = 'queued'
    }
  }
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
        // results are ever kept for a dropped file. Everything dropped
        // here reverts from 'queued' back to 'ready' — otherwise it would
        // show "queued" forever with no worker left to ever process it.
        for (const row of pendingDetection) {
          if (row.status === 'queued') row.status = 'ready'
        }
        pendingDetection.length = 0
        break
      }

      const row = pendingDetection.shift()!
      if (row.status !== 'queued' || row.defaultChannel === null) continue

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
  // Begun synchronously, before awaiting the picker, so the buttons are
  // disabled from the very first click — covering the picker-open window
  // and the import round trip, nothing more.
  beginImport()
  try {
    const paths = (await window.api?.pickFiles()) ?? []
    await importPaths(paths)
  } finally {
    endImport()
  }
}

export async function handleImportFolder(): Promise<void> {
  beginImport()
  try {
    const paths = (await window.api?.pickFolder()) ?? []
    await importPaths(paths)
  } finally {
    endImport()
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
  await openFiles([path])
}

/**
 * Loads several files into the session at once (Home's "Open all" / "Open
 * selected") and opens the FIRST of them for review; the rest appear in the
 * sidebar, ready to click. Files already in this session are not re-imported
 * and their detection is not re-run. The review view shows one file at a
 * time, so "open" for the others means "loaded and one click away".
 */
export async function openFiles(paths: string[]): Promise<void> {
  if (paths.length === 0) return

  beginImport()
  try {
    // Not auto-opened here: the file to show is the first one REQUESTED that
    // can be opened, and it gets recorded as recent below.
    await importPaths(paths, { autoOpen: false })
  } finally {
    endImport()
  }

  for (const path of paths) {
    const row = fileRegistry.find((candidate) => candidate.path === path)
    if (row && row.status !== 'error' && row.defaultChannel !== null) {
      recordRecentFile(row.path, row.filename)
      autoOpenRequest.value = {
        path: row.path,
        channels: row.channels,
        defaultChannel: row.defaultChannel,
      }
      return
    }
  }
}

/**
 * Loads an FP/FN review project's recordings into the session through the
 * exact same import + auto-run detection path as Import Files, in the order
 * given (so the recording opened first is detected first). Nothing is opened
 * here — flagProjects.svelte.ts decides which recording to show — and the
 * files are kept out of Recent files: a project can hold dozens of
 * recordings and would otherwise push everything else off that list.
 * Files already in this session are left as they are.
 */
export async function importForProject(paths: string[]): Promise<string[]> {
  beginImport()
  try {
    return await importPaths(paths, { autoOpen: false, recordRecent: false, fromProject: true })
  } finally {
    endImport()
  }
}

/** Test-only: reset queue state so each test starts from a clean state. */
export function resetForTesting(): void {
  importQueue.importing = false
  importQueue.detecting = false
  importQueue.error = ''
  importQueue.autoRun = true
  activeImports = 0
  inFlight.clear()
  pendingDetection.length = 0
  workerRunning = false
  stopRequested = false
}
