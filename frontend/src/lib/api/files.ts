import { apiGet, apiPost } from './http'
import type { ApiError, EvictResult, FileImportResult, ImportResponse } from './types'

export function importFiles(paths: string[]): Promise<ImportResponse | ApiError> {
  return apiPost('/files/import', 'POST', { paths })
}

export function listFiles(): Promise<FileImportResult[] | ApiError> {
  return apiGet('/files')
}

/**
 * Frees the backend's in-memory copies (signal, beats, windows) of files that
 * left the session. Saved review work in SQLite is untouched; importing a
 * file again reloads it.
 */
export function evictFiles(paths: string[]): Promise<EvictResult> {
  return apiPost('/files/evict', 'POST', { paths })
}
