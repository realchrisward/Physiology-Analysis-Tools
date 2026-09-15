import { apiGet, apiPost } from './http'
import type { ApiError, SettingsPayload, SettingsResult } from './types'

export function getSettings(): Promise<SettingsPayload | ApiError> {
  return apiGet('/settings')
}

export function putSettings(payload: SettingsPayload): Promise<SettingsResult> {
  return apiPost('/settings', 'PUT', payload)
}
