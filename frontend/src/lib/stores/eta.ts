// Learn-as-you-go ETA for the auto-run beat detection queue.
//
// There is no reliable static estimate of how long detection will take for a
// given file, so we never fabricate one. Instead we accumulate real
// (bytes, seconds) samples from completed detections this session and derive
// a running-average throughput from them. Until at least one real sample
// exists, `estimate` returns `null` and the UI must show "Calculating..."
// rather than a hardcoded number presented as real.

let totalBytes = 0
let totalSeconds = 0

/** Accumulate a real (bytes, seconds) sample into the session running total. */
export function recordSample(bytes: number, seconds: number): void {
  totalBytes += bytes
  totalSeconds += seconds
}

/**
 * Estimate the seconds a detection of `bytes` size will take, based on the
 * running-average throughput from samples recorded so far. Returns `null` if
 * no sample has been recorded yet this session.
 */
export function estimate(bytes: number): number | null {
  if (totalSeconds === 0) return null
  const bytesPerSecond = totalBytes / totalSeconds
  return bytes / bytesPerSecond
}

/** Test-only: reset the running total so each test starts from a clean state. */
export function resetForTesting(): void {
  totalBytes = 0
  totalSeconds = 0
}
