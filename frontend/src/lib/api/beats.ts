import { apiPost } from './http'
import type { BeatDetectionResult } from './types'

export function detectBeats(path: string, channel: string): Promise<BeatDetectionResult> {
  return apiPost('/beats/detect', 'POST', { path, channel })
}
