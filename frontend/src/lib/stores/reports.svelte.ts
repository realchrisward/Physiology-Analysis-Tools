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
//  - `epoch[path]` identifies the loaded report: navigation / summary
//    refreshes only apply to the report they were requested for.

import {
  findReportCandidates,
  getReportNavigation,
  getReportState,
  loadReport,
  unloadReport,
} from '../api/reports'
import type { ReportNavigationResult, ReportSummary } from '../api/types'

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
const suggestions: Record<string, { paths: string[]; dismissed: boolean }> = $state({})

const opGen = new Map<string, number>()
const epoch = new Map<string, number>()
const suggestGen = new Map<string, number>()
const queues = new Map<string, Promise<unknown>>()
const navRunning = new Map<string, Promise<void>>()
const navDirty = new Set<string>()

function bump(map: Map<string, number>, path: string): number {
  const next = (map.get(path) ?? 0) + 1
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
  suggestions[path] = { paths: suggestions[path]?.paths ?? [], dismissed: true }
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
  const wasReady = reportSessions[recordingPath]?.status === 'ready'
  setSession(recordingPath, { status: 'loading', summary: null, error: null, reportPath, navigation: null })

  const result = await enqueue(recordingPath, async () => {
    // Superseded while waiting for an earlier call: don't touch the backend.
    if (!isCurrent(recordingPath, op)) return null
    return loadReport(recordingPath, reportPath)
  })
  if (result === null || !isCurrent(recordingPath, op)) return false

  if (result.status === 'ok' && !result.error && result.summary) {
    commitLoaded(recordingPath, result.summary)
    void refreshReportNavigation(recordingPath)
    return true
  }

  const message = failureMessage(result.error, 'Could not read the report')
  // A failed replacement: the backend may well still have the report that was
  // loaded before, and it is better to keep showing it than nothing.
  if (wasReady) {
    const state = await enqueue(recordingPath, () => getReportState(recordingPath))
    if (!isCurrent(recordingPath, op)) return false
    if (state.status === 'ok' && state.summary) {
      bump(epoch, recordingPath)
      setSession(recordingPath, readyFrom(state.summary, `Could not load ${reportPath}: ${message}`))
      void refreshReportNavigation(recordingPath)
      return false
    }
  }
  setSession(recordingPath, { status: 'error', summary: null, error: message, reportPath, navigation: null })
  return false
}

function commitLoaded(recordingPath: string, summary: ReportSummary): void {
  bump(epoch, recordingPath)
  setSession(recordingPath, readyFrom(summary))
  setLink(recordingPath, summary.report_path)
  rememberFolder(summary.report_path)
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
    rememberFolder(state.summary.report_path)
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
  if (result === null || !isCurrent(recordingPath, op)) return

  if (result.status === 'ok' && !result.error && result.summary) {
    commitLoaded(recordingPath, result.summary)
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

/** Detaches the recording's report and forgets its link. */
export async function detachReport(recordingPath: string): Promise<void> {
  bump(opGen, recordingPath)
  bump(epoch, recordingPath)
  // Taking a report away must not bring a suggestion for it straight back.
  dismissReportSuggestion(recordingPath)
  clearLink(recordingPath)
  delete reportSessions[recordingPath]
  // Always asked, even when this side thinks nothing is loaded: after a failed
  // replacement the backend may still hold the previous report.
  await enqueue(recordingPath, () => unloadReport(recordingPath))
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
 * when a report is attached, loading or linked, or when the technician
 * dismissed suggestions for this recording this session.
 */
export async function suggestReport(recordingPath: string): Promise<void> {
  const session = reportSessions[recordingPath]
  if (session && session.status !== 'idle') return
  if (getReportLink(recordingPath)) return
  if (suggestions[recordingPath]?.dismissed) return

  const token = bump(suggestGen, recordingPath)
  const result = await findReportCandidates(recordingPath, [...reportFolders])
  if ((suggestGen.get(recordingPath) ?? 0) !== token) return
  // Something was attached (or linked) while searching.
  const now = reportSessions[recordingPath]
  if ((now && now.status !== 'idle') || getReportLink(recordingPath)) return

  const paths = result.status === 'ok' ? (result.paths ?? []).filter(isNonEmptyString) : []
  suggestions[recordingPath] = { paths, dismissed: false }
}

/** Test-only: reset everything (and clear storage) so each test starts clean. */
export function resetForTesting(): void {
  for (const key of Object.keys(reportLinks)) delete reportLinks[key]
  for (const key of Object.keys(reportSessions)) delete reportSessions[key]
  for (const key of Object.keys(kept)) delete kept[key]
  for (const key of Object.keys(suggestions)) delete suggestions[key]
  reportFolders.length = 0
  opGen.clear()
  epoch.clear()
  suggestGen.clear()
  queues.clear()
  navRunning.clear()
  navDirty.clear()
  try {
    localStorage.removeItem(LINKS_KEY)
    localStorage.removeItem(FOLDERS_KEY)
    localStorage.removeItem(KEPT_KEY)
  } catch {
    // ignore
  }
}
