import { apiGet, apiPost } from './http'
import type { ApiError, SettingsPayload, SettingsResult } from './types'

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
