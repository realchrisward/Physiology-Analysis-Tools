import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ImportScreen from './ImportScreen.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

describe('ImportScreen', () => {
  it('imports files via the file picker and shows a ready row', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/57.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
          {
            path: '/data/57.txt',
            filename: '57.txt',
            status: 'ok',
            channels: ['channel 1'],
            time_column: 'ts',
            size: 12345,
            modified_time: 1.0,
            default_channel: 'channel 1',
            default_channel_matched_rule: true,
            error: null,
          },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      const rows = screen.getAllByTestId('file-row')
      expect(rows).toHaveLength(1)
      expect(rows[0]).toHaveTextContent('57.txt')
      expect(rows[0]).toHaveTextContent('ready')
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/files/import',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ paths: ['/data/57.txt'] }),
      }),
    )
  })

  it('imports files via the folder picker and shows a row per file', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue([]),
      pickFolder: vi.fn().mockResolvedValue(['/data/a.txt', '/data/b.txt']),
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
          {
            path: '/data/a.txt',
            filename: 'a.txt',
            status: 'ok',
            channels: ['channel 1'],
            time_column: 'ts',
            size: 100,
            modified_time: 1.0,
            default_channel: 'channel 1',
            default_channel_matched_rule: true,
            error: null,
          },
          {
            path: '/data/b.txt',
            filename: 'b.txt',
            status: 'ok',
            channels: ['channel 1'],
            time_column: 'ts',
            size: 200,
            modified_time: 1.0,
            default_channel: 'channel 1',
            default_channel_matched_rule: true,
            error: null,
          },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-folder-button'))

    await waitFor(() => {
      const rows = screen.getAllByTestId('file-row')
      expect(rows).toHaveLength(2)
      expect(rows[0]).toHaveTextContent('a.txt')
      expect(rows[1]).toHaveTextContent('b.txt')
    })
  })

  it('isolates per-file errors: one row ready, the other shows its error', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/57.txt', '/data/bad.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
          {
            path: '/data/57.txt',
            filename: '57.txt',
            status: 'ok',
            channels: ['channel 1'],
            time_column: 'ts',
            size: 12345,
            modified_time: 1.0,
            default_channel: 'channel 1',
            default_channel_matched_rule: true,
            error: null,
          },
          {
            path: '/data/bad.txt',
            filename: 'bad.txt',
            status: 'error',
            channels: [],
            time_column: null,
            size: null,
            modified_time: null,
            default_channel: null,
            default_channel_matched_rule: false,
            error: 'No extractor succeeded',
          },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      const rows = screen.getAllByTestId('file-row')
      expect(rows).toHaveLength(2)
      expect(rows[0]).toHaveTextContent('57.txt')
      expect(rows[0]).toHaveTextContent('ready')
      expect(rows[0]).not.toHaveTextContent('No extractor succeeded')
      expect(rows[1]).toHaveTextContent('bad.txt')
      expect(rows[1]).toHaveTextContent('No extractor succeeded')
    })
  })

  it('is a no-op when the file picker is canceled (empty result)', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue([]),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))

    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryAllByTestId('file-row')).toHaveLength(0)
  })

  it('shows a clean error banner when the import request fails at the network level', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/57.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network error')))

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('import-error')).toHaveTextContent('network error')
    })
    expect(screen.queryAllByTestId('file-row')).toHaveLength(0)
  })
})
