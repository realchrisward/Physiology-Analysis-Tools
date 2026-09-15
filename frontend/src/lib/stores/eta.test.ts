import { beforeEach, describe, expect, it } from 'vitest'
import { estimate, recordSample, resetForTesting } from './eta'

// resetForTesting() clears the session running total between tests so each
// test starts from the "no samples yet" state.
beforeEach(() => {
  resetForTesting()
})

describe('eta', () => {
  it('returns null when no sample has been recorded yet', () => {
    expect(estimate(1000)).toBeNull()
  })

  it('estimates from a single recorded sample', () => {
    recordSample(1000, 2) // 1000 bytes / 2s = 500 bytes/sec
    expect(estimate(2000)).toBe(4) // 2000 / 500
  })

  it('computes a running average across multiple samples', () => {
    recordSample(1000, 2)
    recordSample(3000, 3)
    // total: 4000 bytes / 5s = 800 bytes/sec
    expect(estimate(1600)).toBe(2) // 1600 / 800
  })
})
