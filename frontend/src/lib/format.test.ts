import { describe, expect, it } from 'vitest'
import { formatEditTime } from './format'

describe('formatEditTime', () => {
  it('shows the day and the time of a valid timestamp', () => {
    const text = formatEditTime('2026-09-24T14:32:10+00:00')
    expect(text).toContain('2026')
    expect(text).toMatch(/\d{1,2}:\d{2}/)
  })

  it('shows an unreadable value as it came', () => {
    expect(formatEditTime('not a date')).toBe('not a date')
  })
})
