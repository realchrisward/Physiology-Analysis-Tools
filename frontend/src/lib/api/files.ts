import { apiGet, apiPost } from './http'
import type { ApiError, FileImportResult, ImportResponse } from './types'

export function importFiles(paths: string[]): Promise<ImportResponse | ApiError> {
  return apiPost('/files/import', 'POST', { paths })
}

export function listFiles(): Promise<FileImportResult[] | ApiError> {
  return apiGet('/files')
}
