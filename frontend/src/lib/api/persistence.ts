import { afterReviewChange } from '../reviewChanges'
import { apiGet, apiPost } from './http'
import type {
  BadDataAddResult,
  BadDataDeleteResult,
  BadDataUpdateResult,
  BeatAddResult,
  BeatSnapResult,
  BeatDeleteResult,
  CategoryUpdateResult,
  ChannelLabelResult,
  ChannelPersistResult,
  DiscardStateResult,
  FileStateResult,
  FileStateSummaryResult,
  FileSummariesResult,
  PersistBeatsResult,
  ReportResult,
  RestoreBackupResult,
} from './types'

export function getFileState(path: string): Promise<FileStateResult> {
  return apiGet('/files/state', { path })
}

// Counts only (beats, reviewed beats, bad-data marks) plus any backup: what
// replacing the saved review would discard.
export function getFileStateSummary(path: string): Promise<FileStateSummaryResult> {
  return apiGet('/files/state/summary', { path })
}

// Puts back the review a report replaced (the newest unexpired backup).
export function restoreReviewBackup(path: string): Promise<RestoreBackupResult> {
  return afterReviewChange(apiPost('/files/state/restore-backup', 'POST', { path }), path)
}

export function putChannel(path: string, channel: string): Promise<ChannelPersistResult> {
  return afterReviewChange(apiPost('/files/channel', 'PUT', { path, channel }), path)
}

// Gives `channel` a display name in each of `paths`; `null` (or a blank name)
// goes back to the original. Only the shown name changes — the channel keeps
// its own name as its key everywhere else.
export function putChannelLabel(paths: string[], channel: string, label: string | null): Promise<ChannelLabelResult> {
  return afterReviewChange(apiPost('/files/channel-label', 'PUT', { paths, channel, label }), ...paths)
}

export function persistBeats(path: string, channel: string): Promise<PersistBeatsResult> {
  return afterReviewChange(apiPost('/files/beats', 'POST', { path, channel }), path)
}

// `uncertain` rides along with confirm / reject / reassign (saved together with
// the decision; omitted keeps the beat's current mark). `set_uncertain` changes
// only that mark, and only on a beat already confirmed or rejected.
// `seen` marks an unreviewed beat reviewed (looked at, no decision);
// `clear_decision` takes a decision off (the beat stays reviewed); `unreview`
// puts the beat back to unreviewed. Neither of the last two works while the
// beat is marked uncertain.
export type BeatReviewAction =
  | 'confirm'
  | 'reject'
  | 'reassign'
  | 'remove_flag'
  | 'set_uncertain'
  | 'seen'
  | 'clear_decision'
  | 'unreview'

export function updateBeatCategory(
  path: string,
  ts: number,
  action: BeatReviewAction,
  category?: string,
  uncertain?: boolean | null,
): Promise<CategoryUpdateResult> {
  return afterReviewChange(apiPost('/files/beats/category', 'PATCH', { path, ts, action, category, uncertain }), path)
}

export function addBadData(path: string, start: number, stop: number): Promise<BadDataAddResult> {
  return apiPost('/files/bad-data', 'POST', { path, start, stop })
}

export function updateBadData(
  path: string,
  id: number,
  start: number,
  stop: number,
): Promise<BadDataUpdateResult> {
  return apiPost('/files/bad-data', 'PATCH', { path, id, start, stop })
}

export function deleteBadData(path: string, id: number): Promise<BadDataDeleteResult> {
  return apiPost('/files/bad-data', 'DELETE', { path, id })
}

// Where a beat added near `ts` belongs: the R peak the click was aiming at.
// `slack` is how far off the click may be, in seconds (a few pixels at the
// current zoom). Read-only — nothing is saved until `addBeat`.
export function snapBeat(path: string, channel: string, ts: number, slack?: number): Promise<BeatSnapResult> {
  return apiPost('/files/beats/snap', 'POST', { path, channel, ts, slack })
}

// Adds a beat the detector missed, at the sample nearest `ts`.
export function addBeat(path: string, channel: string, ts: number): Promise<BeatAddResult> {
  return afterReviewChange(apiPost('/files/beats/one', 'POST', { path, channel, ts }), path)
}

// Removes a beat that isn't really a beat (a detection false positive) —
// distinct from rejecting it, which keeps the beat and only clears its
// arrhythmia flags.
export function deleteBeat(path: string, ts: number): Promise<BeatDeleteResult> {
  return afterReviewChange(apiPost('/files/beats/one', 'DELETE', { path, ts }), path)
}

// Throws away everything saved for a file so detection can start clean.
export function discardFileState(path: string): Promise<DiscardStateResult> {
  return afterReviewChange(apiPost('/files/state', 'DELETE', { path }), path)
}

// Saved-review counts for many recordings at once (they need not be imported):
// what the sidebar and Home sort and filter by.
export function getFileSummaries(paths: string[]): Promise<FileSummariesResult> {
  return apiPost('/files/summaries', 'POST', { paths })
}

export function generateReport(path: string, outputPath: string): Promise<ReportResult> {
  return apiPost('/files/report', 'POST', { path, output_path: outputPath })
}
