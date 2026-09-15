import { apiGet, apiPost } from './http'
import type { SettingsPayload, SettingsResult } from './types'

export function getSettings(): Promise<SettingsPayload> {
  return apiGet('/settings')
}

export function putSettings(payload: SettingsPayload): Promise<SettingsResult> {
  return apiPost('/settings', 'PUT', payload)
}
