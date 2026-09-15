import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetForTesting } from '../../lib/stores/eta'
import ImportScreen from './ImportScreen.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
  resetForTesting()
})

function importOkResponse(files: Array<{ path: string; filename: string; size: number; defaultChannel: string }>) {
  return {
    ok: true,
    json: async () => ({
      results: files.map((f) => ({
        path: f.path,
        filename: f.filename,
        status: 'ok',
        channels: [f.defaultChannel],
        time_column: 'ts',
        size: f.size,
        modified_time: 1.0,
        default_channel: f.defaultChannel,
        default_channel_matched_rule: true,
        error: null,
      })),
    }),
  }
}

function beatsOkResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: async () => ({
      status: 'ok',
      beats: [],
      count: 15,
      mean_hr: 65,
      duration: 30,
      elapsed_seconds: 0.5,
      file_size_bytes: 45877,
      error: null,
      ...overrides,
    }),
  }
}

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

    // This test is scoped to import mechanics, not beat detection; auto-run
    // is covered separately below.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
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

    // This test is scoped to import mechanics, not beat detection; auto-run
    // is covered separately below.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
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

    // This test is scoped to import mechanics, not beat detection; auto-run
    // is covered separately below.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
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

describe('ImportScreen auto-run beat detection', () => {
  it('defaults the auto-run checkbox to checked', () => {
    render(ImportScreen)
    const checkbox = screen.getByTestId('auto-run-checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(true)
  })

  it('runs beat detection sequentially against each file default channel', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/a.txt', '/data/b.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/files/import')) {
        return Promise.resolve(
          importOkResponse([
            { path: '/data/a.txt', filename: 'a.txt', size: 100, defaultChannel: 'channel 1' },
            { path: '/data/b.txt', filename: 'b.txt', size: 200, defaultChannel: 'channel 1' },
          ]),
        )
      }
      if (url.endsWith('/beats/detect')) {
        const call = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/beats/detect')).length
        return Promise.resolve(
          call === 1
            ? beatsOkResponse({ count: 15, mean_hr: 65 })
            : beatsOkResponse({ count: 30, mean_hr: 70 }),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      const rows = screen.getAllByTestId('file-row')
      expect(rows[0]).toHaveTextContent('15')
      expect(rows[1]).toHaveTextContent('30')
    })

    const detectCalls = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/beats/detect'))
    expect(detectCalls).toHaveLength(2)
    expect(detectCalls[0]).toEqual([
      'http://127.0.0.1:8000/beats/detect',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ path: '/data/a.txt', channel: 'channel 1' }),
      }),
    ])
    expect(detectCalls[1]).toEqual([
      'http://127.0.0.1:8000/beats/detect',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ path: '/data/b.txt', channel: 'channel 1' }),
      }),
    ])
  })

  it('shows "Calculating..." ETA for the first file, then a real ETA for the second', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/a.txt', '/data/b.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }

    let resolveFirstDetect: (value: unknown) => void
    const firstDetectPromise = new Promise((resolve) => {
      resolveFirstDetect = resolve
    })

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/files/import')) {
        return Promise.resolve(
          importOkResponse([
            { path: '/data/a.txt', filename: 'a.txt', size: 100, defaultChannel: 'channel 1' },
            { path: '/data/b.txt', filename: 'b.txt', size: 200, defaultChannel: 'channel 1' },
          ]),
        )
      }
      if (url.endsWith('/beats/detect')) {
        const call = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/beats/detect')).length
        if (call === 1) return firstDetectPromise
        return Promise.resolve(beatsOkResponse({ count: 30, mean_hr: 70 }))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('eta-badge')).toHaveTextContent('Calculating')
    })

    resolveFirstDetect!(beatsOkResponse({ count: 15, mean_hr: 65 }))

    await waitFor(() => {
      const rows = screen.getAllByTestId('file-row')
      expect(rows[0]).toHaveTextContent('15')
    })

    await waitFor(() => {
      const badge = screen.getByTestId('eta-badge')
      expect(badge).not.toHaveTextContent('Calculating')
    })
  })

  it('skips detection entirely when auto-run is unchecked', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/a.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/files/import')) {
        return Promise.resolve(
          importOkResponse([{ path: '/data/a.txt', filename: 'a.txt', size: 100, defaultChannel: 'channel 1' }]),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      const rows = screen.getAllByTestId('file-row')
      expect(rows).toHaveLength(1)
      expect(rows[0]).toHaveTextContent('ready')
    })

    const detectCalls = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/beats/detect'))
    expect(detectCalls).toHaveLength(0)
  })

  it('Stop cancels the queue but lets the in-flight file finish', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/a.txt', '/data/b.txt', '/data/c.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }

    let resolveFirstDetect: (value: unknown) => void
    const firstDetectPromise = new Promise((resolve) => {
      resolveFirstDetect = resolve
    })

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/files/import')) {
        return Promise.resolve(
          importOkResponse([
            { path: '/data/a.txt', filename: 'a.txt', size: 100, defaultChannel: 'channel 1' },
            { path: '/data/b.txt', filename: 'b.txt', size: 200, defaultChannel: 'channel 1' },
            { path: '/data/c.txt', filename: 'c.txt', size: 300, defaultChannel: 'channel 1' },
          ]),
        )
      }
      if (url.endsWith('/beats/detect')) {
        return firstDetectPromise
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      const detectCalls = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/beats/detect'))
      expect(detectCalls).toHaveLength(1)
    })

    await fireEvent.click(screen.getByTestId('stop-button'))

    resolveFirstDetect!(beatsOkResponse({ count: 15, mean_hr: 65 }))

    await waitFor(() => {
      const rows = screen.getAllByTestId('file-row')
      expect(rows[0]).toHaveTextContent('15')
    })

    const rows = screen.getAllByTestId('file-row')
    expect(rows[1]).toHaveTextContent('ready')
    expect(rows[2]).toHaveTextContent('ready')

    const detectCalls = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/beats/detect'))
    expect(detectCalls).toHaveLength(1)
  })

  it('disables the import buttons while a detection queue is running, then re-enables them', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/a.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }

    let resolveDetect: (value: unknown) => void
    const detectPromise = new Promise((resolve) => {
      resolveDetect = resolve
    })

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/files/import')) {
        return Promise.resolve(
          importOkResponse([{ path: '/data/a.txt', filename: 'a.txt', size: 100, defaultChannel: 'channel 1' }]),
        )
      }
      if (url.endsWith('/beats/detect')) {
        return detectPromise
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('import-files-button')).toBeDisabled()
      expect(screen.getByTestId('import-folder-button')).toBeDisabled()
    })

    resolveDetect!(beatsOkResponse({ count: 15, mean_hr: 65 }))

    await waitFor(() => {
      expect(screen.getByTestId('import-files-button')).not.toBeDisabled()
      expect(screen.getByTestId('import-folder-button')).not.toBeDisabled()
    })
  })

  it('disables the import buttons as soon as the picker is invoked, before it resolves', async () => {
    let resolvePickFiles: (value: string[]) => void
    const pickFilesPromise = new Promise<string[]>((resolve) => {
      resolvePickFiles = resolve
    })
    ;(window as any).api = {
      pickFiles: vi.fn().mockReturnValue(pickFilesPromise),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    // Uncheck auto-run up front — the checkbox itself becomes disabled the
    // instant the queue goes busy, so it must be set before that click.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    // The picker promise is still pending — nothing has resolved yet — but
    // the buttons must already be disabled from the very first click.
    await waitFor(() => {
      expect(screen.getByTestId('import-files-button')).toBeDisabled()
      expect(screen.getByTestId('import-folder-button')).toBeDisabled()
    })
    expect(fetchMock).not.toHaveBeenCalled()

    resolvePickFiles!([])

    await waitFor(() => {
      expect(screen.getByTestId('import-files-button')).not.toBeDisabled()
      expect(screen.getByTestId('import-folder-button')).not.toBeDisabled()
    })
  })

  it('disables the import buttons while the import request is in flight, before it resolves', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/a.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }

    let resolveImport: (value: unknown) => void
    const importPromise = new Promise((resolve) => {
      resolveImport = resolve
    })
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/files/import')) return importPromise
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    // The picker has already resolved (synchronously mocked) but the
    // import fetch is still pending — buttons must stay disabled through
    // this window too.
    await waitFor(() => {
      expect(screen.getByTestId('import-files-button')).toBeDisabled()
      expect(screen.getByTestId('import-folder-button')).toBeDisabled()
    })

    resolveImport!(
      importOkResponse([{ path: '/data/a.txt', filename: 'a.txt', size: 100, defaultChannel: 'channel 1' }]),
    )

    await waitFor(() => {
      expect(screen.getByTestId('import-files-button')).not.toBeDisabled()
      expect(screen.getByTestId('import-folder-button')).not.toBeDisabled()
    })
  })
})

describe('ImportScreen Review action', () => {
  it('shows a Review button for a ready row with a channel, and fires onReview with path/channels/defaultChannel', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/57.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockResolvedValue(
      importOkResponse([{ path: '/data/57.txt', filename: '57.txt', size: 12345, defaultChannel: 'channel 1' }]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const onReview = vi.fn()
    render(ImportScreen, { props: { onReview } })

    // Scoped to Review-button mechanics, not auto-run detection.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('review-button')).toBeInTheDocument()
    })

    await fireEvent.click(screen.getByTestId('review-button'))

    expect(onReview).toHaveBeenCalledWith({
      path: '/data/57.txt',
      channels: ['channel 1'],
      defaultChannel: 'channel 1',
    })
  })

  it('shows no Review button for a row that failed to import', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/bad.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
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

    render(ImportScreen, { props: { onReview: vi.fn() } })

    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('file-row')).toHaveTextContent('bad.txt')
    })

    expect(screen.queryByTestId('review-button')).not.toBeInTheDocument()
  })
})
