import { apiGet, apiPost } from './http'
import type {
  BadDataAddResult,
  BadDataDeleteResult,
  CategoryUpdateResult,
  ChannelPersistResult,
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

export function deleteBadData(path: string, id: number): Promise<BadDataDeleteResult> {
  return apiPost('/files/bad-data', 'DELETE', { path, id })
}

export function generateReport(path: string, outputPath: string): Promise<ReportResult> {
  return apiPost('/files/report', 'POST', { path, output_path: outputPath })
}
