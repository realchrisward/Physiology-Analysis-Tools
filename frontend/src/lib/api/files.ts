import { apiGet, apiPost } from './http'
import type { FileImportResult, ImportResponse } from './types'

export function importFiles(paths: string[]): Promise<ImportResponse> {
  return apiPost('/files/import', 'POST', { paths })
}

export function listFiles(): Promise<FileImportResult[]> {
  return apiGet('/files')
}
