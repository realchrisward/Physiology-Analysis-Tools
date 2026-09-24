import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  addBeat,
  deleteBadData,
  generateReport,
  getFileState,
  snapBeat,
  updateBadData,
  updateBeatCategory,
} from './persistence'

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

  it('updateBadData PATCHes the mark id with its new range', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) })
    vi.stubGlobal('fetch', fetchMock)

    await updateBadData('/a.txt', 7, 1.5, 2.5)

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/bad-data', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/a.txt', id: 7, start: 1.5, stop: 2.5 }),
    })
  })

  it('deleteBadData DELETEs by mark id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) })
    vi.stubGlobal('fetch', fetchMock)

    await deleteBadData('/a.txt', 7)

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/bad-data', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/a.txt', id: 7 }),
    })
  })

  it('snapBeat POSTs the channel and rough position to the read-only snap route', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok', ts: 4.2 }) })
    vi.stubGlobal('fetch', fetchMock)

    const result = await snapBeat('/a.txt', 'channel 1', 4.19)

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/beats/snap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/a.txt', channel: 'channel 1', ts: 4.19 }),
    })
    expect(result.ts).toBe(4.2)
  })

  it('addBeat POSTs to /files/beats/one', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) })
    vi.stubGlobal('fetch', fetchMock)

    await addBeat('/a.txt', 'channel 1', 4.2)

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/beats/one', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/a.txt', channel: 'channel 1', ts: 4.2 }),
    })
  })

  it('generateReport sends output_path (snake_case) not outputPath', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await generateReport('/a.txt', '/out/report.xlsx')

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/a.txt', output_path: '/out/report.xlsx' }),
    })
  })
})
