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

import { formatRelativeTime } from './format'

describe('formatRelativeTime', () => {
  const now = new Date(2026, 8, 24, 15, 0, 0).getTime()
  const ago = (ms: number) => formatRelativeTime(now - ms, now)
  const MIN = 60_000
  const HOUR = 60 * MIN

  it('says just now for the last minute, and for clock skew into the future', () => {
    expect(ago(20_000)).toBe('just now')
    expect(formatRelativeTime(now + 5 * MIN, now)).toBe('just now')
  })

  it('counts minutes and hours with correct singular/plural', () => {
    expect(ago(MIN)).toBe('1 minute ago')
    expect(ago(45 * MIN)).toBe('45 minutes ago')
    expect(ago(HOUR + 5 * MIN)).toBe('1 hour ago')
    expect(ago(5 * HOUR)).toBe('5 hours ago')
  })

  it('uses calendar days: last night is yesterday from the next morning', () => {
    const lateLastNight = new Date(2026, 8, 23, 23, 50).getTime()
    const nextMorning = new Date(2026, 8, 24, 8, 0).getTime()
    expect(formatRelativeTime(lateLastNight, nextMorning)).toMatch(/^yesterday at /)
    // ...but twenty minutes across midnight is still just minutes.
    const justAfterMidnight = new Date(2026, 8, 24, 0, 10).getTime()
    expect(formatRelativeTime(lateLastNight, justAfterMidnight)).toBe('20 minutes ago')
  })

  it('counts days up to a week, then shows the date', () => {
    expect(ago(3 * 24 * HOUR)).toBe('3 days ago')
    expect(ago(20 * 24 * HOUR)).toContain('2026')
  })
})
