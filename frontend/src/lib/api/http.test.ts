import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPost } from './http'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

describe('apiGet', () => {
  it('passes through a successful JSON response', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await apiGet<{ status: string }>('/health')

    expect(result).toEqual({ status: 'ok' })
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/health')
  })

  it('synthesizes a clean error on network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network error')))

    const result = await apiGet<{ status: string; error?: string }>('/health')

    expect(result).toEqual({ status: 'error', error: 'network error' })
  })

  it('synthesizes a clean error on a non-2xx HTTP response instead of crashing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ detail: 'boom' }),
      }),
    )

    const result = await apiGet<{ status: string; error?: string }>('/health')

    expect(result.status).toBe('error')
    expect(result.error).toBeTruthy()
  })

  it('escapes special characters when building the query string', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await apiGet('/files/state', { path: '/Users/x/9 long.txt', channel: 'channel 1' })

    const calledUrl = fetchMock.mock.calls[0][0] as string
    expect(calledUrl).toContain('9%20long.txt')
    expect(calledUrl).toContain('channel%201')
  })
})

describe('apiPost', () => {
  it('sends a JSON body with the correct method and headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await apiPost('/files/import', 'POST', { paths: ['a.txt'] })

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: ['a.txt'] }),
    })
  })
})
