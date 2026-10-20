import { apiGet, apiPost } from './http'
import type {
  ReportBeatResult,
  ReportCandidatesResult,
  ReportLoadResult,
  ReportNavigationResult,
  ReportWindowResult,
} from './types'

/** Parses an xlsx report and attaches it to an imported recording (replacing any previous one). */
export function loadReport(recordingPath: string, reportPath: string): Promise<ReportLoadResult> {
  return apiPost('/reports/load', 'POST', { recording_path: recordingPath, report_path: reportPath })
}

/** Detaches the recording's report. */
export function unloadReport(recordingPath: string): Promise<{ status: string; error?: string | null }> {
  return apiPost('/reports/unload', 'POST', { recording_path: recordingPath })
}

/** The report currently attached to a recording (`summary` null when none). */
export function getReportState(recordingPath: string): Promise<ReportLoadResult> {
  return apiGet('/reports/state', { path: recordingPath })
}

/**
 * Report beats in [start, end], matched against the recording's current
 * beats. Label-0 (normal) beats only when `includeNormal`.
 */
export function getReportWindow(
  recordingPath: string,
  start: number,
  end: number,
  includeNormal: boolean = false,
): Promise<ReportWindowResult> {
  return apiGet('/reports/window', { path: recordingPath, start, end, include_normal: includeNormal })
}

/** Every navigation target per filter, plus match statistics. */
export function getReportNavigation(recordingPath: string): Promise<ReportNavigationResult> {
  return apiGet('/reports/navigation', { path: recordingPath })
}

/** The report's entry for the beat at `ts` (nearest within tolerance), if any. */
export function getReportBeat(recordingPath: string, ts: number): Promise<ReportBeatResult> {
  return apiGet('/reports/beat', { path: recordingPath, ts })
}

/** Reports named after the recording, next to it or in the given folders. */
export function findReportCandidates(recordingPath: string, folders: string[]): Promise<ReportCandidatesResult> {
  return apiPost('/reports/candidates', 'POST', { recording_path: recordingPath, folders })
}
