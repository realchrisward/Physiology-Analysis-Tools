import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fileRegistry, resetForTesting as resetFileRegistry } from '../../lib/stores/fileRegistry.svelte'
import { resetForTesting as resetImportQueue } from '../../lib/stores/importQueue.svelte'
import { recentFiles, resetForTesting as resetRecentFiles } from '../../lib/stores/recentFiles.svelte'
import { resetForTesting as resetEta } from '../../lib/stores/eta'
import ImportScreen from './ImportScreen.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
  resetEta()
  resetFileRegistry()
  resetImportQueue()
  resetRecentFiles()
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

// The imported-file LIST rendering (file-row/review-button/eta-badge) moved
// to Sidebar.svelte — see Sidebar.test.ts. These tests cover what
// ImportScreen itself still owns: the hero, the import/detect trigger
// mechanics (now living in lib/stores/importQueue.svelte.ts), and the
// shared `fileRegistry`/`recentFiles` stores it populates.
describe('ImportScreen', () => {
  it('imports files via the file picker and populates the shared file registry', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/57.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockResolvedValue(
      importOkResponse([{ path: '/data/57.txt', filename: '57.txt', size: 12345, defaultChannel: 'channel 1' }]),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    // This test is scoped to import mechanics, not beat detection; auto-run
    // is covered separately below.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(fileRegistry).toHaveLength(1)
      expect(fileRegistry[0].filename).toBe('57.txt')
      expect(fileRegistry[0].status).toBe('ready')
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/files/import',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ paths: ['/data/57.txt'] }),
      }),
    )
    // A successfully imported file is also recorded as a recent file.
    expect(recentFiles).toHaveLength(1)
    expect(recentFiles[0].path).toBe('/data/57.txt')
  })

  it('imports files via the folder picker and populates a row per file', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue([]),
      pickFolder: vi.fn().mockResolvedValue(['/data/a.txt', '/data/b.txt']),
    }
    const fetchMock = vi.fn().mockResolvedValue(
      importOkResponse([
        { path: '/data/a.txt', filename: 'a.txt', size: 100, defaultChannel: 'channel 1' },
        { path: '/data/b.txt', filename: 'b.txt', size: 200, defaultChannel: 'channel 1' },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-folder-button'))

    await waitFor(() => {
      expect(fileRegistry).toHaveLength(2)
      expect(fileRegistry[0].filename).toBe('a.txt')
      expect(fileRegistry[1].filename).toBe('b.txt')
    })
  })

  it('isolates per-file errors: one row ready, the other carries its error', async () => {
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

    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(fileRegistry).toHaveLength(2)
      expect(fileRegistry[0].status).toBe('ready')
      expect(fileRegistry[1].status).toBe('error')
      expect(fileRegistry[1].error).toBe('No extractor succeeded')
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
    expect(fileRegistry).toHaveLength(0)
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
    expect(fileRegistry).toHaveLength(0)
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
      expect(fileRegistry[0].beatCount).toBe(15)
      expect(fileRegistry[1].beatCount).toBe(30)
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
      expect(fileRegistry).toHaveLength(1)
      expect(fileRegistry[0].status).toBe('ready')
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
      expect(fileRegistry[0].beatCount).toBe(15)
    })

    expect(fileRegistry[1].status).toBe('ready')
    expect(fileRegistry[2].status).toBe('ready')

    const detectCalls = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/beats/detect'))
    expect(detectCalls).toHaveLength(1)
  })

  // A technician must be able to import more files at any moment, even
  // while an earlier batch's auto-run detection is still churning in the
  // background — see lib/stores/importQueue.svelte.ts. The Import buttons
  // are only briefly disabled for the picker + `/files/import` round trip.
  it('keeps the import buttons enabled while a detection queue runs in the background, and shows Stop', async () => {
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

    // Detection is now running in the background (Stop appears) but the
    // Import buttons are NOT disabled by it.
    await waitFor(() => {
      expect(screen.getByTestId('stop-button')).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByTestId('import-files-button')).not.toBeDisabled()
      expect(screen.getByTestId('import-folder-button')).not.toBeDisabled()
    })

    resolveDetect!(beatsOkResponse({ count: 15, mean_hr: 65 }))

    await waitFor(() => {
      expect(screen.queryByTestId('stop-button')).not.toBeInTheDocument()
    })
  })

  it('lets a second import proceed immediately while an earlier detection queue is still running', async () => {
    ;(window as any).api = {
      pickFiles: vi
        .fn()
        .mockResolvedValueOnce(['/data/a.txt'])
        .mockResolvedValueOnce(['/data/b.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }

    let resolveFirstDetect: (value: unknown) => void
    const firstDetectPromise = new Promise((resolve) => {
      resolveFirstDetect = resolve
    })

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/files/import')) {
        const call = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/files/import')).length
        return Promise.resolve(
          call === 1
            ? importOkResponse([{ path: '/data/a.txt', filename: 'a.txt', size: 100, defaultChannel: 'channel 1' }])
            : importOkResponse([{ path: '/data/b.txt', filename: 'b.txt', size: 100, defaultChannel: 'channel 1' }]),
        )
      }
      if (url.endsWith('/beats/detect')) {
        const call = fetchMock.mock.calls.filter((c: any) => c[0].endsWith('/beats/detect')).length
        return call === 1 ? firstDetectPromise : Promise.resolve(beatsOkResponse({ count: 8, mean_hr: 60 }))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    await fireEvent.click(screen.getByTestId('import-files-button'))
    await waitFor(() => expect(fileRegistry).toHaveLength(1))

    // A second import, triggered while the first file's detection is still
    // in flight, must not be blocked by it.
    await fireEvent.click(screen.getByTestId('import-files-button'))
    await waitFor(() => expect(fileRegistry).toHaveLength(2))
    expect(fileRegistry[1].filename).toBe('b.txt')

    resolveFirstDetect!(beatsOkResponse({ count: 15, mean_hr: 65 }))

    // The second file's detection is enqueued behind the first and still
    // runs (rather than being dropped) once the first finishes.
    await waitFor(() => expect(fileRegistry[1].beatCount).toBe(8))
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

describe('ImportScreen recent files', () => {
  it('shows no recent files section until at least one file has been imported', () => {
    render(ImportScreen)
    expect(screen.queryByTestId('recent-file-card')).not.toBeInTheDocument()
  })

  // Answers "is beat detection run every time a file from recents is
  // opened?" — previously yes, unconditionally (a real bug: re-importing
  // and re-running detection on a file already loaded this session).
  it('opens an already-imported recent file directly — no re-import, no re-run of detection', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/57.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockResolvedValue(
      importOkResponse([{ path: '/data/57.txt', filename: '57.txt', size: 12345, defaultChannel: 'channel 1' }]),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(ImportScreen)

    // Auto-open (see App.svelte) means the very first import already opens
    // this file — uncheck auto-run and use the auto-open request itself
    // (not a second click) as the signal the file is ready, so this test
    // stays focused on the recent-file-card's own behavior.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('recent-file-card')).toHaveTextContent('57.txt')
    })

    const { autoOpenRequest } = await import('../../lib/stores/fileRegistry.svelte')
    autoOpenRequest.value = null // clear the initial import's own auto-open request
    fetchMock.mockClear()

    await fireEvent.click(screen.getByTestId('recent-file-card'))

    await waitFor(() => {
      expect(autoOpenRequest.value).toEqual({
        path: '/data/57.txt',
        channels: ['channel 1'],
        defaultChannel: 'channel 1',
      })
    })
    // No new import, and therefore no re-run of detection.
    expect(fetchMock).not.toHaveBeenCalled()
    expect((window as any).api.pickFiles).toHaveBeenCalledTimes(1)

    autoOpenRequest.value = null
  })

  it('re-imports (and re-runs detection for) a recent file no longer in this session — e.g. after a restart', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      importOkResponse([{ path: '/data/57.txt', filename: '57.txt', size: 12345, defaultChannel: 'channel 1' }]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { recordRecentFile } = await import('../../lib/stores/recentFiles.svelte')
    recordRecentFile('/data/57.txt', '57.txt')

    render(ImportScreen)

    await waitFor(() => {
      expect(screen.getByTestId('recent-file-card')).toHaveTextContent('57.txt')
    })

    await fireEvent.click(screen.getByTestId('recent-file-card'))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://127.0.0.1:8000/files/import',
        expect.objectContaining({ body: JSON.stringify({ paths: ['/data/57.txt'] }) }),
      )
    })
  })
})
