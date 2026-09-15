import { afterEach, describe, expect, it, vi } from 'vitest'
import { getSettings } from './settings'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

describe('settings api', () => {
  it('getSettings resolves to an ApiError shape (not a crash) on network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network error')))

    const result = await getSettings()

    expect('error' in result).toBe(true)
    if ('error' in result) {
      expect(result.status).toBe('error')
      expect(result.error).toBeTruthy()
    }
  })
})
