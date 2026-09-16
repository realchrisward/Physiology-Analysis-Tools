import { afterEach, describe, expect, it } from 'vitest'
import { resetForTesting, setTheme, themeState } from './theme.svelte'

afterEach(() => {
  resetForTesting()
})

describe('theme store', () => {
  it('defaults to system', () => {
    expect(themeState.mode).toBe('system')
  })

  it('setTheme updates the mode and persists it', () => {
    setTheme('dark')
    expect(themeState.mode).toBe('dark')
    expect(localStorage.getItem('pat.theme')).toBe('dark')
  })

  it('accepts the black & white mode', () => {
    setTheme('bw')
    expect(themeState.mode).toBe('bw')
  })
})
