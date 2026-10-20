// Report labels: an xlsx report (e.g. exported by the original PySide app, or
// edited outside this software) whose per-beat labels are shown read-only over
// the open recording, matched to our own detected beats. Our saved review is
// never modified by it.
//
// State lives in three places:
//  - `reportLinks`: recording path -> the report path last loaded for it,
//    persisted to localStorage, so the report loads again by itself when the
//    recording is opened later (this session or the next).
//  - `reportFolders`: the folders reports were loaded from (most recent
//    first, max 5), persisted. The picker opens in the first one and
//    `suggestReport` searches all of them for a report named after the
//    recording.
//  - `reportSessions`: what the backend currently has for each recording this
//    session (status, summary, navigation targets). Not persisted.
//
// Everything here is keyed by the RECORDING path, never by what the review
// screen is showing: the workspace is remounted per file, so a response that
// arrives after the technician switched files lands in the right recording's
// slot instead of the wrong screen. Three guards keep late or overlapping
// responses from applying a stale result:
//  - `opGen[path]` is bumped by every attach / restore / detach. An async step
//    only writes if its generation is still current.
//  - backend calls for one recording run strictly one after another
//    (`enqueue`), so the order the backend applies load/unload in is the order
//    they were issued in. A step superseded before it starts never calls.
//    Removing a report (or cancelling its load) is the one exception: it must
//    not wait behind a request that may never come back (a report on an
//    offline share), so it drops the chain and goes out at once; the backend
//    discards a load that a later unload superseded.
//  - `epoch[path]` identifies the loaded report: navigation / summary
//    refreshes only apply to the report they were requested for.
// Generation numbers come from one counter that never repeats, so forgetting a
// recording (`forgetReportSessions`) can drop its entries without a late
// response ever matching a number handed out afterwards.
//
// The backend is the source of truth for what is attached: whenever an attempt
// to attach fails, this side asks it what it holds and shows exactly that.

import {
  findReportCandidates,
  getReportNavigation,
  getReportState,
  loadReport,
  unloadReport,
} from '../api/reports'
import type { ReportLoadResult, ReportNavigationResult, ReportSummary } from '../api/types'

export type ReportStatus = 'idle' | 'loading' | 'ready' | 'error' | 'missing'

export interface ReportSession {
  // idle    - nothing attached.
  // loading - a load (picked, dropped, suggested or restored from a link) is in flight.
  // ready   - attached; `summary` is set. `error` may still hold a note about a
  //           failed attempt to replace it (the previous report was kept).
  // error   - the attempt to attach `reportPath` failed.
  // missing - the report this recording was linked to could not be read
  //           (moved, deleted, locked). The link is kept until the user removes it.
  status: ReportStatus
  summary: ReportSummary | null
  error: string | null
  // The report this session is about: the loaded one, or the one that failed.
  reportPath: string | null
  navigation: ReportNavigationResult | null
}

const LINKS_KEY = 'pat.reportLinks'
const FOLDERS_KEY = 'pat.reportFolders'
const KEPT_KEY = 'pat.reportKept'
const PENDING_UNLOAD_KEY = 'pat.reportPendingUnload'
const MAX_FOLDERS = 5
// Links accumulate one per recording ever given a report; the oldest go first.
const MAX_LINKS = 500

const IDLE: ReportSession = Object.freeze({
  status: 'idle',
  summary: null,
  error: null,
  reportPath: null,
  navigation: null,
}) as ReportSession

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0
}

// Persisted maps are rebuilt entry by entry rather than trusted: a hand-edited
// or older profile must never make this module throw at import time.
function loadStringMap(key: string): Record<string, string> {
  const out: Record<string, string> = {}
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return out
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return out
    for (const [recording, report] of Object.entries(parsed as Record<string, unknown>)) {
      if (recording === '__proto__' || !isNonEmptyString(recording) || !isNonEmptyString(report)) continue
      out[recording] = report
    }
  } catch {
    // Unreadable or unavailable storage: start with nothing.
  }
  return out
}

function loadFolders(): string[] {
  try {
    const raw = localStorage.getItem(FOLDERS_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const folders: string[] = []
    for (const entry of parsed) {
      if (isNonEmptyString(entry) && !folders.some((f) => sameFolder(f, entry))) folders.push(entry)
    }
    return folders.slice(0, MAX_FOLDERS)
  } catch {
    return []
  }
}

// Recordings whose report was removed here but never confirmed gone by the backend.
function loadPendingUnload(): Set<string> {
  const out = new Set<string>()
  try {
    const raw = localStorage.getItem(PENDING_UNLOAD_KEY)
    if (!raw) return out
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return out
    for (const entry of parsed) {
      if (isNonEmptyString(entry)) out.add(entry)
      if (out.size >= MAX_LINKS) break
    }
  } catch {
    // Unreadable or unavailable storage: start with nothing.
  }
  return out
}

function save(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // localStorage unavailable or full: links simply won't survive a restart
    // (they still work for the rest of this session); nothing depends on this.
  }
}

/** recording path -> report path, persisted. */
export const reportLinks: Record<string, string> = $state(loadStringMap(LINKS_KEY))

/** Folders reports were loaded from, most recent first (max 5), persisted. */
export const reportFolders: string[] = $state(loadFolders())

/** Per-recording session state; read it through `getReportSession`. */
export const reportSessions: Record<string, ReportSession> = $state({})

// Reports the technician confirmed despite the file name not matching the
// recording's ("Keep"), so reopening the recording doesn't ask again.
const kept: Record<string, string> = $state(loadStringMap(KEPT_KEY))

// Suggestions found for a recording this session; `dismissed` is also set
// whenever a report gets attached or removed, so nothing is suggested twice.
// `folders` is what was searched (the report folders at the time), so an
// answer, even an empty one, is not asked for again until that changes.
const suggestions: Record<string, { paths: string[]; dismissed: boolean; folders: string | null }> = $state({})

// Recordings whose report was removed but whose `/reports/unload` failed: the
// backend may still hold it, so it must not come back as the recording's
// report. Persisted; retried the next time the recording is opened.
const pendingUnload: Set<string> = loadPendingUnload()

// Why removing a recording's report failed, shown next to the Load button.
const unloadErrors: Record<string, string> = $state({})

let generation = 0
const opGen = new Map<string, number>()
const epoch = new Map<string, number>()
const suggestGen = new Map<string, number>()
// The op number of the last remove / cancel, while nothing newer has happened.
const detachOps = new Map<string, number>()
const queues = new Map<string, Promise<unknown>>()
const navRunning = new Map<string, Promise<void>>()
const navDirty = new Set<string>()

function bump(map: Map<string, number>, path: string): number {
  const next = ++generation
  map.set(path, next)
  return next
}

function sameFolder(a: string, b: string): boolean {
  const trim = (value: string) => value.replace(/[\\/]+$/, '')
  const left = trim(a)
  const right = trim(b)
  // Drive-letter paths are case-insensitive on Windows.
  if (/^[a-z]:/i.test(left) && /^[a-z]:/i.test(right)) return left.toLowerCase() === right.toLowerCase()
  return left === right
}

function folderOf(filePath: string): string | null {
  const cut = Math.max(filePath.lastIndexOf('/'), filePath.lastIndexOf('\\'))
  if (cut < 0) return null
  // "/report.xlsx" -> "/", "C:\report.xlsx" -> "C:\".
  const folder = filePath.slice(0, cut)
  if (folder === '') return filePath.slice(0, 1)
  return /^[a-z]:$/i.test(folder) ? `${folder}${filePath[cut]}` : folder
}

/** Remembers the folder a report came from: first in the list, deduplicated, capped. */
function rememberFolder(reportPath: string): void {
  const folder = folderOf(reportPath)
  if (!folder) return
  const existing = reportFolders.findIndex((f) => sameFolder(f, folder))
  if (existing === 0) return
  if (existing > 0) reportFolders.splice(existing, 1)
  reportFolders.unshift(folder)
  if (reportFolders.length > MAX_FOLDERS) reportFolders.length = MAX_FOLDERS
  save(FOLDERS_KEY, reportFolders)
}

function setLink(recordingPath: string, reportPath: string): void {
  if (reportLinks[recordingPath] === reportPath) return
  // Re-insert so the entry counts as the newest.
  delete reportLinks[recordingPath]
  reportLinks[recordingPath] = reportPath
  const keys = Object.keys(reportLinks)
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_LINKS))) delete reportLinks[key]
  save(LINKS_KEY, reportLinks)
}

function clearLink(recordingPath: string): void {
  if (typeof reportLinks[recordingPath] === 'string') {
    delete reportLinks[recordingPath]
    save(LINKS_KEY, reportLinks)
  }
  if (typeof kept[recordingPath] === 'string') {
    delete kept[recordingPath]
    save(KEPT_KEY, kept)
  }
}

function setPendingUnload(recordingPath: string, pending: boolean): void {
  if (pendingUnload.has(recordingPath) === pending) return
  if (pending) pendingUnload.add(recordingPath)
  else pendingUnload.delete(recordingPath)
  save(PENDING_UNLOAD_KEY, [...pendingUnload])
}

// Runs a backend call after every earlier one for this recording finished.
function enqueue<T>(path: string, task: () => Promise<T>): Promise<T> {
  const previous = queues.get(path) ?? Promise.resolve()
  const run = previous.then(task, task)
  queues.set(
    path,
    run.then(
      () => undefined,
      () => undefined,
    ),
  )
  return run
}

function isCurrent(path: string, op: number): boolean {
  return (opGen.get(path) ?? 0) === op
}

function setSession(path: string, session: ReportSession): void {
  reportSessions[path] = session
}

function readyFrom(summary: ReportSummary, error: string | null = null): ReportSession {
  return { status: 'ready', summary, error, reportPath: summary.report_path, navigation: null }
}

function failureMessage(error: string | null | undefined, fallback: string): string {
  return error && error.length > 0 ? error : fallback
}

// Supersedes everything in flight for the recording: results of earlier calls
// are ignored from now on, and what is asked next no longer waits behind a
// request that may never come back.
function invalidate(recordingPath: string): number {
  bump(epoch, recordingPath)
  queues.delete(recordingPath)
  return bump(opGen, recordingPath)
}

// Takes the recording's report off the backend. A refusal is remembered (and
// shown) so the report is not mistaken for the recording's again later; only
// the newest operation for the recording gets to say so.
async function unloadBackend(recordingPath: string, op: number): Promise<boolean> {
  const result = await enqueue(recordingPath, () => unloadReport(recordingPath))
  const done = result.status === 'ok' && !result.error
  if (!isCurrent(recordingPath, op)) return done
  if (done) {
    setPendingUnload(recordingPath, false)
    delete unloadErrors[recordingPath]
  } else {
    setPendingUnload(recordingPath, true)
    unloadErrors[recordingPath] =
      `Could not remove the report (${failureMessage(result.error, 'no answer from the backend')}). ` +
      'It will be tried again when this recording is opened.'
  }
  return done
}

// A load that finished after its report was removed (or its loading cancelled)
// put the report back on the backend: take it off again. Only while removing
// is still the latest thing that happened to the recording.
function undoLateLoad(recordingPath: string, result: ReportLoadResult | null): void {
  if (!result || result.status !== 'ok' || result.error || !result.summary) return
  const removedAt = detachOps.get(recordingPath)
  if (removedAt === undefined || removedAt !== opGen.get(recordingPath)) return
  void unloadBackend(recordingPath, removedAt)
}

/** The recording's report session (an idle one when there is none). Reactive. */
export function getReportSession(path: string): ReportSession {
  const session = reportSessions[path]
  return session && typeof session === 'object' && 'status' in session ? session : IDLE
}

/** The report path this recording is linked to, if any. Reactive. */
export function getReportLink(path: string): string | null {
  const link = reportLinks[path]
  return typeof link === 'string' ? link : null
}

/** The suggested report for a recording, unless dismissed. Reactive. */
export function getReportSuggestion(path: string): string | null {
  const entry = suggestions[path]
  if (!entry || entry.dismissed) return null
  return entry.paths[0] ?? null
}

/** Why removing the recording's report failed on the backend, if it did. Reactive. */
export function getReportUnloadError(path: string): string | null {
  const message = unloadErrors[path]
  return typeof message === 'string' ? message : null
}

/** Hides the note about a failed removal (the removal is still retried on the next open). */
export function dismissReportUnloadError(path: string): void {
  delete unloadErrors[path]
}

/** Whether the technician kept this report although its name doesn't match the recording. */
export function isReportKept(path: string, reportPath: string): boolean {
  return kept[path] === reportPath
}

/** Confirms a report whose file name differs from the recording's. */
export function keepReport(path: string): void {
  const session = reportSessions[path]
  if (!session || session.status !== 'ready' || !session.summary) return
  kept[path] = session.summary.report_path
  save(KEPT_KEY, kept)
}

/** Clears the note about a failed attempt to replace the loaded report. */
export function dismissReportError(path: string): void {
  const session = reportSessions[path]
  if (session && session.status === 'ready' && session.error) session.error = null
}

/** Stops suggesting a report for this recording for the rest of the session. */
export function dismissReportSuggestion(path: string): void {
  bump(suggestGen, path)
  suggestions[path] = {
    paths: suggestions[path]?.paths ?? [],
    dismissed: true,
    folders: suggestions[path]?.folders ?? null,
  }
}

/**
 * Loads `reportPath` for the recording, replacing any report already attached.
 * On success the report is linked to the recording, its folder remembered and
 * the navigation targets fetched. Returns whether it ended up loaded.
 */
export async function attachReport(recordingPath: string, reportPath: string): Promise<boolean> {
  const op = bump(opGen, recordingPath)
  bump(epoch, recordingPath)
  dismissReportSuggestion(recordingPath)
  setSession(recordingPath, { status: 'loading', summary: null, error: null, reportPath, navigation: null })

  const result = await enqueue(recordingPath, async () => {
    // Superseded while waiting for an earlier call: don't touch the backend.
    if (!isCurrent(recordingPath, op)) return null
    return loadReport(recordingPath, reportPath)
  })
  if (result === null) return false
  if (!isCurrent(recordingPath, op)) {
    undoLateLoad(recordingPath, result)
    return false
  }

  if (result.status === 'ok' && !result.error && result.summary) {
    commitLoaded(recordingPath, result.summary, true)
    void refreshReportNavigation(recordingPath)
    return true
  }

  // The load failed, or lost to a later one (which the backend may report as
  // an error). Either way the backend, not this call, knows what it holds now
  // (the report from before, one from a replacement that overtook this one, or
  // nothing): show that.
  const message = failureMessage(result.error, 'Could not read the report')
  const state = await enqueue(recordingPath, async () => {
    if (!isCurrent(recordingPath, op)) return null
    return getReportState(recordingPath)
  })
  if (state === null || !isCurrent(recordingPath, op)) return false

  let held = state.status === 'ok' ? (state.summary ?? null) : null
  // A report that was removed but never confirmed gone is not what is attached.
  if (held && pendingUnload.has(recordingPath)) held = null
  if (held) {
    bump(epoch, recordingPath)
    const same = held.report_path === reportPath
    setSession(recordingPath, readyFrom(held, same ? null : `Could not load ${reportPath}: ${message}`))
    setLink(recordingPath, held.report_path)
    void refreshReportNavigation(recordingPath)
    return same
  }
  setSession(recordingPath, { status: 'error', summary: null, error: message, reportPath, navigation: null })
  return false
}

// `remember`: the folder is only worth remembering for a report the technician
// chose, not for one restored by itself.
function commitLoaded(recordingPath: string, summary: ReportSummary, remember: boolean): void {
  bump(epoch, recordingPath)
  setSession(recordingPath, readyFrom(summary))
  setLink(recordingPath, summary.report_path)
  setPendingUnload(recordingPath, false)
  delete unloadErrors[recordingPath]
  if (remember) rememberFolder(summary.report_path)
}

/**
 * Run when a recording is opened. The backend may still have its report (the
 * app stayed running); otherwise a linked report is loaded again silently. A
 * linked file that can't be read leaves the session `missing` (the link stays
 * until the technician removes it).
 */
export async function restoreLinkedReport(recordingPath: string): Promise<void> {
  const current = reportSessions[recordingPath]
  // A load for this recording is already in flight and will land by itself.
  if (current?.status === 'loading') return

  const op = bump(opGen, recordingPath)
  const wasReady = current?.status === 'ready'

  // The report was removed here but the backend never confirmed it: finish
  // that first, instead of taking the backend's copy for the recording's report.
  if (pendingUnload.has(recordingPath)) {
    const gone = await unloadBackend(recordingPath, op)
    if (!gone || !isCurrent(recordingPath, op)) return
  }

  const state = await enqueue(recordingPath, async () => {
    if (!isCurrent(recordingPath, op)) return null
    return getReportState(recordingPath)
  })
  if (state === null || !isCurrent(recordingPath, op)) return

  if (state.status === 'ok' && state.summary) {
    if (wasReady && reportSessions[recordingPath]?.summary?.report_path === state.summary.report_path) {
      // Same report as before: refresh what may have changed, keep the rest.
      const session = reportSessions[recordingPath]
      session.summary = state.summary
    } else {
      bump(epoch, recordingPath)
      setSession(recordingPath, readyFrom(state.summary))
    }
    setLink(recordingPath, state.summary.report_path)
    void refreshReportNavigation(recordingPath)
    return
  }

  const link = getReportLink(recordingPath)
  if (!link) {
    // Nothing on the backend and nothing linked. A report that WAS ready is gone
    // (the backend restarted without a link to restore it from).
    if (wasReady) {
      bump(epoch, recordingPath)
      delete reportSessions[recordingPath]
    }
    return
  }

  bump(epoch, recordingPath)
  setSession(recordingPath, { status: 'loading', summary: null, error: null, reportPath: link, navigation: null })
  const result = await enqueue(recordingPath, async () => {
    if (!isCurrent(recordingPath, op)) return null
    return loadReport(recordingPath, link)
  })
  if (result === null) return
  if (!isCurrent(recordingPath, op)) {
    undoLateLoad(recordingPath, result)
    return
  }

  if (result.status === 'ok' && !result.error && result.summary) {
    commitLoaded(recordingPath, result.summary, false)
    void refreshReportNavigation(recordingPath)
  } else {
    setSession(recordingPath, {
      status: 'missing',
      summary: null,
      error: failureMessage(result.error, 'Could not read the linked report'),
      reportPath: link,
      navigation: null,
    })
  }
}

/**
 * Detaches the recording's report and forgets its link. Goes to the backend at
 * once rather than behind a load that is still waiting for a slow disk or
 * share; that load's result is ignored (and undone if it lands anyway).
 */
export async function detachReport(recordingPath: string): Promise<void> {
  const op = invalidate(recordingPath)
  detachOps.set(recordingPath, op)
  // Taking a report away must not bring a suggestion for it straight back.
  dismissReportSuggestion(recordingPath)
  clearLink(recordingPath)
  delete reportSessions[recordingPath]
  // Always asked, even when this side thinks nothing is loaded: after a failed
  // replacement the backend may still hold the previous report.
  await unloadBackend(recordingPath, op)
}

/**
 * Gives up on the report that is being read ("Cancel" while loading), e.g. a
 * file on a share that went offline. Unlike removing it, the recording's link
 * is kept: the report is unavailable (`missing`, with Retry) rather than
 * unlinked, so cancelling a restore does not lose it.
 */
export async function cancelReportLoad(recordingPath: string): Promise<void> {
  if (reportSessions[recordingPath]?.status !== 'loading') return
  const op = invalidate(recordingPath)
  detachOps.set(recordingPath, op)
  const link = getReportLink(recordingPath)
  if (link) {
    setSession(recordingPath, {
      status: 'missing',
      summary: null,
      error: 'Reading the report was cancelled',
      reportPath: link,
      navigation: null,
    })
  } else {
    delete reportSessions[recordingPath]
  }
  // The read may still finish on the backend; this makes it discard the result.
  await unloadBackend(recordingPath, op)
}

/**
 * The recordings left the session (the file list was cleared, or an FP/FN
 * project closed): drop everything held for them, so a recording imported again
 * starts from a clean slate instead of a stale error chip, and a long session
 * does not keep every recording's navigation targets. The link and the "kept"
 * mark are the technician's, not session state, and stay. Anything still in
 * flight for these recordings finds its generation gone and writes nothing.
 */
export function forgetReportSessions(paths: string[]): void {
  for (const path of paths) {
    opGen.delete(path)
    epoch.delete(path)
    suggestGen.delete(path)
    detachOps.delete(path)
    queues.delete(path)
    navDirty.delete(path)
    delete reportSessions[path]
    delete suggestions[path]
    delete unloadErrors[path]
  }
}

/**
 * Re-fetches the navigation targets and match statistics (they depend on OUR
 * beats, which change as the technician reviews). Concurrent calls coalesce:
 * at most one request is in flight per recording, and everything asked for
 * meanwhile is answered by exactly one more.
 */
export function refreshReportNavigation(recordingPath: string): Promise<void> {
  if (reportSessions[recordingPath]?.status !== 'ready') return Promise.resolve()
  const running = navRunning.get(recordingPath)
  if (running) {
    navDirty.add(recordingPath)
    return running
  }

  const run = (async () => {
    try {
      do {
        navDirty.delete(recordingPath)
        const forEpoch = epoch.get(recordingPath) ?? 0
        const result = await getReportNavigation(recordingPath)
        const session = reportSessions[recordingPath]
        // The report was replaced or removed while this was in flight.
        if (!session || session.status !== 'ready' || (epoch.get(recordingPath) ?? 0) !== forEpoch) continue
        session.navigation = result
      } while (navDirty.has(recordingPath))
    } finally {
      navRunning.delete(recordingPath)
    }
  })()
  navRunning.set(recordingPath, run)
  return run
}

/** Re-reads the summary (the settings comparison depends on the current detection settings). */
export async function refreshReportSummary(recordingPath: string): Promise<void> {
  const before = reportSessions[recordingPath]
  if (!before || before.status !== 'ready') return
  const forEpoch = epoch.get(recordingPath) ?? 0
  const state = await getReportState(recordingPath)
  const session = reportSessions[recordingPath]
  if (!session || session.status !== 'ready' || (epoch.get(recordingPath) ?? 0) !== forEpoch) return
  if (state.status === 'ok' && state.summary && state.summary.report_path === session.summary?.report_path) {
    session.summary = state.summary
  }
}

/**
 * Looks for a report named after the recording (next to it or in the folders
 * reports were loaded from) and records it for `getReportSuggestion`. Skipped
 * when a report is attached, loading or linked, when the technician dismissed
 * suggestions for this recording this session, or when this session already
 * searched for it in the same folders (an empty answer included: opening a
 * recording that has no report must not scan the disk every time).
 */
export async function suggestReport(recordingPath: string): Promise<void> {
  const session = reportSessions[recordingPath]
  if (session && session.status !== 'idle') return
  if (getReportLink(recordingPath)) return
  const known = suggestions[recordingPath]
  if (known?.dismissed) return

  const folders = [...reportFolders]
  const searched = JSON.stringify(folders)
  if (known && known.folders === searched) return

  const token = bump(suggestGen, recordingPath)
  const result = await findReportCandidates(recordingPath, folders)
  if ((suggestGen.get(recordingPath) ?? 0) !== token) return
  // Something was attached (or linked) while searching.
  const now = reportSessions[recordingPath]
  if ((now && now.status !== 'idle') || getReportLink(recordingPath)) return

  const paths = result.status === 'ok' ? (result.paths ?? []).filter(isNonEmptyString) : []
  suggestions[recordingPath] = { paths, dismissed: false, folders: searched }
}

/** Test-only: reset everything (and clear storage) so each test starts clean. */
export function resetForTesting(): void {
  for (const key of Object.keys(reportLinks)) delete reportLinks[key]
  for (const key of Object.keys(reportSessions)) delete reportSessions[key]
  for (const key of Object.keys(kept)) delete kept[key]
  for (const key of Object.keys(suggestions)) delete suggestions[key]
  for (const key of Object.keys(unloadErrors)) delete unloadErrors[key]
  pendingUnload.clear()
  reportFolders.length = 0
  opGen.clear()
  epoch.clear()
  suggestGen.clear()
  detachOps.clear()
  queues.clear()
  navRunning.clear()
  navDirty.clear()
  try {
    localStorage.removeItem(LINKS_KEY)
    localStorage.removeItem(FOLDERS_KEY)
    localStorage.removeItem(KEPT_KEY)
    localStorage.removeItem(PENDING_UNLOAD_KEY)
  } catch {
    // ignore
  }
}
