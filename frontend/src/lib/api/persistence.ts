import { apiGet, apiPost } from './http'
import type {
  BadDataAddResult,
  BadDataDeleteResult,
  BadDataUpdateResult,
  BeatAddResult,
  BeatSnapResult,
  BeatDeleteResult,
  CategoryUpdateResult,
  ChannelPersistResult,
  DiscardStateResult,
  FileStateResult,
  PersistBeatsResult,
  ReportResult,
} from './types'

export function getFileState(path: string): Promise<FileStateResult> {
  return apiGet('/files/state', { path })
}

export function putChannel(path: string, channel: string): Promise<ChannelPersistResult> {
  return apiPost('/files/channel', 'PUT', { path, channel })
}

export function persistBeats(path: string, channel: string): Promise<PersistBeatsResult> {
  return apiPost('/files/beats', 'POST', { path, channel })
}

export function updateBeatCategory(
  path: string,
  ts: number,
  action: 'confirm' | 'reject' | 'reassign' | 'remove_flag',
  category?: string,
): Promise<CategoryUpdateResult> {
  return apiPost('/files/beats/category', 'PATCH', { path, ts, action, category })
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

// Where a beat added near `ts` belongs: the signal peak nearest the click.
// Read-only — nothing is saved until `addBeat`.
export function snapBeat(path: string, channel: string, ts: number): Promise<BeatSnapResult> {
  return apiPost('/files/beats/snap', 'POST', { path, channel, ts })
}

// Adds a beat the detector missed, at the sample nearest `ts`.
export function addBeat(path: string, channel: string, ts: number): Promise<BeatAddResult> {
  return apiPost('/files/beats/one', 'POST', { path, channel, ts })
}

// Removes a beat that isn't really a beat (a detection false positive) —
// distinct from rejecting it, which keeps the beat and only clears its
// arrhythmia flags.
export function deleteBeat(path: string, ts: number): Promise<BeatDeleteResult> {
  return apiPost('/files/beats/one', 'DELETE', { path, ts })
}

// Throws away everything saved for a file so detection can start clean.
export function discardFileState(path: string): Promise<DiscardStateResult> {
  return apiPost('/files/state', 'DELETE', { path })
}

export function generateReport(path: string, outputPath: string): Promise<ReportResult> {
  return apiPost('/files/report', 'POST', { path, output_path: outputPath })
}
