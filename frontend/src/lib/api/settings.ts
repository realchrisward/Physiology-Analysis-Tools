import { apiGet, apiPost } from './http'
import type {
  ApiError,
  DatabaseInfoResult,
  DatabaseResetResult,
  SettingsPayload,
  SettingsResult,
} from './types'

export function getSettings(): Promise<SettingsPayload | ApiError> {
  return apiGet('/settings')
}

// Factory defaults, for the dialog's "Restore Defaults" button. Fetching
// them does NOT apply them — the technician still has to save.
export function getDefaultSettings(): Promise<SettingsPayload | ApiError> {
  return apiGet('/settings/defaults')
}

export function putSettings(payload: SettingsPayload): Promise<SettingsResult> {
  return apiPost('/settings', 'PUT', payload)
}

// Deletes the on-disk database file outright and recreates an empty one in
// its place - every file's beats, review decisions, and bad-data marks are
// gone for good. See the Settings dialog's Danger Zone confirmation.
export function deleteDatabase(): Promise<DatabaseResetResult> {
  return apiPost('/settings/database', 'DELETE')
}

// Where the review database lives on disk and roughly what's in it, for the
// Settings dialog's Database tab.
export function getDatabaseInfo(): Promise<DatabaseInfoResult> {
  return apiGet('/settings/database')
}
