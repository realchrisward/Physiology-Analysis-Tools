import { afterEach, describe, expect, it, vi } from 'vitest'
import { generateReport, getFileState, updateBeatCategory } from './persistence'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

describe('persistence api', () => {
  it('getFileState GETs /files/state with an encoded path query string', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', found: true }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await getFileState('/Users/x/9 long.txt')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/files/state?path=%2FUsers%2Fx%2F9%20long.txt',
    )
  })

  it('updateBeatCategory PATCHes with the full body including an omitted category', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await updateBeatCategory('/a.txt', 12.5, 'confirm')

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/beats/category', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/a.txt', ts: 12.5, action: 'confirm', category: undefined }),
    })
  })

  it('generateReport sends output_dir (snake_case) not outputDir', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await generateReport('/a.txt', '/out')

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/a.txt', output_dir: '/out' }),
    })
  })
})
