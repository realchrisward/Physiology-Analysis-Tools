import { afterEach, describe, expect, it, vi } from 'vitest'
import { importFiles, listFiles } from './files'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

describe('files api', () => {
  it('importFiles POSTs to /files/import with {paths}', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await importFiles(['/a.txt', '/b.txt'])

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: ['/a.txt', '/b.txt'] }),
    })
  })

  it('listFiles GETs /files', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    })
    vi.stubGlobal('fetch', fetchMock)

    await listFiles()

    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/files')
  })
})
