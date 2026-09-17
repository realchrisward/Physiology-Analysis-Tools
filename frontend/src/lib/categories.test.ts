import { describe, expect, it } from 'vitest'
import { primaryDisplayCategory } from './categories'

describe('primaryDisplayCategory', () => {
  it('returns "unevaluated" when any_arrhythmia is null', () => {
    expect(primaryDisplayCategory({ any_arrhythmia: null })).toBe('unevaluated')
  })

  it('returns "unevaluated" when any_arrhythmia is missing entirely', () => {
    expect(primaryDisplayCategory({})).toBe('unevaluated')
  })

  it('returns "normal" when any_arrhythmia is false', () => {
    expect(primaryDisplayCategory({ any_arrhythmia: false, tachycardia_absolute: true })).toBe('normal')
  })

  it('returns the one flagged category when any_arrhythmia is true', () => {
    expect(primaryDisplayCategory({ any_arrhythmia: true, prem_beat: true })).toBe('prem_beat')
  })

  it('picks by fixed priority when multiple categories are flagged', () => {
    // bradycardia_absolute is first in REASSIGNABLE_CATEGORIES' order.
    expect(
      primaryDisplayCategory({
        any_arrhythmia: true,
        tachycardia_absolute: true,
        bradycardia_absolute: true,
      }),
    ).toBe('bradycardia_absolute')
  })

  it('falls back to "other_arrhythmia" when any_arrhythmia is true but no specific flag is set', () => {
    expect(primaryDisplayCategory({ any_arrhythmia: true })).toBe('other_arrhythmia')
  })
})
