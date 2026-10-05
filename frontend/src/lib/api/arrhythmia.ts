import { apiPost } from './http'
import type { ArrhythmiaDetectionResult } from './types'

// The two methods a technician can run from the UI. 'both' is still accepted
// by the backend (the automatic on-load run uses it) but has no button of its
// own.
export type ArrhythmiaMethod = 'heuristic' | 'unsupervised'

export function detectArrhythmias(
  path: string,
  channel: string,
  method: ArrhythmiaMethod | 'both',
): Promise<ArrhythmiaDetectionResult> {
  return apiPost('/arrhythmia/detect', 'POST', { path, channel, method })
}
