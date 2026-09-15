import { apiPost } from './http'
import type { ArrhythmiaDetectionResult } from './types'

export function detectArrhythmias(
  path: string,
  channel: string,
  method: 'heuristic' | 'unsupervised' | 'both',
): Promise<ArrhythmiaDetectionResult> {
  return apiPost('/arrhythmia/detect', 'POST', { path, channel, method })
}
