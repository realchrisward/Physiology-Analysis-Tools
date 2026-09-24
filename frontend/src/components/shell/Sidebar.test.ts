import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { autoOpenRequest, fileRegistry, resetForTesting as resetFileRegistry, type FileRow } from '../../lib/stores/fileRegistry.svelte'
import { resetForTesting as resetImportQueue } from '../../lib/stores/importQueue.svelte'
import { recordRecentFile, resetForTesting as resetRecentFiles } from '../../lib/stores/recentFiles.svelte'
import { resetForTesting as resetEta } from '../../lib/stores/eta'
import Sidebar from './Sidebar.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
  resetEta()
  resetFileRegistry()
  resetImportQueue()
  resetRecentFiles()
})

function pushRow(overrides: Partial<FileRow> = {}): FileRow {
  const row: FileRow = {
    path: '/data/57.txt',
    filename: '57.txt',
    status: 'ready',
    channels: ['channel 1'],
    defaultChannel: 'channel 1',
    size: 12345,
    error: null,
    beatCount: null,
    meanHr: null,
    ...overrides,
  }
  fileRegistry.push(row)
  return fileRegistry[fileRegistry.length - 1]
}

// Sidebar is a pure read-plus-click consumer of the shared `fileRegistry`
// store (populated elsewhere by ImportScreen/importQueue.svelte.ts — see
// ImportScreen.test.ts for that side) — so these tests drive the store
// directly rather than going through a full import flow.
describe('Sidebar file list', () => {
  it('labels the main list "Files", and labels the two import buttons distinctly', () => {
    render(Sidebar)

    expect(screen.getByText('Files')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-import-files-button')).toHaveTextContent('Import Files')
    expect(screen.getByTestId('sidebar-import-folder-button')).toHaveTextContent('Import Folder')
  })

  it('renders a row per registered file, showing status text', () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt', status: 'ready' })
    pushRow({ path: '/data/bad.txt', filename: 'bad.txt', status: 'error', defaultChannel: null, error: 'No extractor succeeded' })

    render(Sidebar)

    const rows = screen.getAllByTestId('file-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('57.txt')
    expect(rows[0]).toHaveTextContent('ready')
    expect(rows[0]).not.toHaveTextContent('No extractor succeeded')
    expect(rows[1]).toHaveTextContent('bad.txt')
    expect(rows[1]).toHaveTextContent('No extractor succeeded')
  })

  it('shows a distinct "queued" status, and still lets the file be opened, for a file waiting in the detection queue', () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt', status: 'queued' })

    render(Sidebar, { props: { onReview: vi.fn() } })

    const row = screen.getByTestId('file-row')
    expect(row).toHaveTextContent('queued for detection')
    expect(row).toHaveAttribute('aria-disabled', 'false')
  })

  it('shows "Calculating..." ETA for the first detecting file, then a real ETA for the next', async () => {
    pushRow({ path: '/data/a.txt', filename: 'a.txt', status: 'detecting', size: 100 })

    render(Sidebar)

    expect(screen.getByTestId('eta-badge')).toHaveTextContent('Calculating')

    // A real throughput sample becomes available once the first file's
    // detection completes elsewhere; a second file entering 'detecting'
    // afterwards gets a real estimate instead of "Calculating...".
    const { recordSample } = await import('../../lib/stores/eta')
    recordSample(1000, 1)
    pushRow({ path: '/data/b.txt', filename: 'b.txt', status: 'detecting', size: 200 })

    await waitFor(() => {
      const badges = screen.getAllByTestId('eta-badge')
      expect(badges[1]).not.toHaveTextContent('Calculating')
    })
  })

  it('opens a file when its row is clicked, firing onReview with path/channels/defaultChannel', async () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt', channels: ['channel 1'], defaultChannel: 'channel 1' })
    const onReview = vi.fn()

    render(Sidebar, { props: { onReview } })

    expect(screen.queryByTestId('review-button')).not.toBeInTheDocument()
    await fireEvent.click(screen.getByTestId('file-row'))

    expect(onReview).toHaveBeenCalledWith({
      path: '/data/57.txt',
      channels: ['channel 1'],
      defaultChannel: 'channel 1',
    })
  })

  it('does not open a row that failed to import', async () => {
    pushRow({ path: '/data/bad.txt', filename: 'bad.txt', status: 'error', defaultChannel: null, channels: [] })
    const onReview = vi.fn()

    render(Sidebar, { props: { onReview } })

    const row = screen.getByTestId('file-row')
    expect(row).toHaveAttribute('aria-disabled', 'true')
    await fireEvent.click(row)
    expect(onReview).not.toHaveBeenCalled()
  })

  it('opens the focused row from the keyboard', async () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt', channels: ['channel 1'], defaultChannel: 'channel 1' })
    const onReview = vi.fn()
    render(Sidebar, { props: { onReview } })

    await fireEvent.keyDown(screen.getByTestId('file-row'), { key: 'Enter' })

    expect(onReview).toHaveBeenCalledTimes(1)
  })

  it('removes a file with its X without opening it, and tells the shell', async () => {
    pushRow({ path: '/data/a.txt', filename: 'a.txt', channels: ['channel 1'], defaultChannel: 'channel 1' })
    pushRow({ path: '/data/b.txt', filename: 'b.txt', channels: ['channel 1'], defaultChannel: 'channel 1' })
    const onReview = vi.fn()
    const onFileRemoved = vi.fn()
    render(Sidebar, { props: { onReview, onFileRemoved } })

    await fireEvent.click(screen.getAllByTestId('remove-file-button')[0])

    expect(onReview).not.toHaveBeenCalled()
    expect(onFileRemoved).toHaveBeenCalledWith('/data/a.txt')
    await waitFor(() => expect(screen.getAllByTestId('file-row')).toHaveLength(1))
    expect(screen.getByTestId('file-row')).toHaveTextContent('b.txt')
  })

  it('takes a queued file out of the detection queue when it is removed', async () => {
    pushRow({ path: '/data/a.txt', filename: 'a.txt', status: 'queued' })
    const row = fileRegistry[0]
    render(Sidebar)

    await fireEvent.click(screen.getByTestId('remove-file-button'))

    expect(row.status).toBe('ready')
  })

  it('shows when a file was last opened, in words', () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt' })
    recordRecentFile('/data/57.txt', '57.txt')

    render(Sidebar)

    expect(screen.getByTestId('file-row-opened')).toHaveTextContent('Opened just now')
  })

  it('shows no opened line for a file with no open history', () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt' })

    render(Sidebar)

    expect(screen.queryByTestId('file-row-opened')).not.toBeInTheDocument()
  })

  it('highlights the active file and hides row detail when collapsed', () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt' })

    const { container } = render(Sidebar, { props: { collapsed: true, activePath: '/data/57.txt' } })

    // Collapsed mode hides the filename/status/Review button — only the
    // status dot remains, with the filename reachable via the row's title.
    expect(screen.queryByText('57.txt')).not.toBeInTheDocument()
    expect(screen.queryByTestId('remove-file-button')).not.toBeInTheDocument()
    expect(container.querySelector('.sidebar.collapsed')).toBeInTheDocument()
  })

  it('shows an empty state when no files have been imported', () => {
    render(Sidebar)
    expect(screen.getByText('No files imported yet')).toBeInTheDocument()
  })
})

describe('Sidebar import controls', () => {
  it('triggers the file picker via the compact import button', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue([]),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    vi.stubGlobal('fetch', vi.fn())

    render(Sidebar)

    await fireEvent.click(screen.getByTestId('sidebar-import-files-button'))

    expect((window as any).api.pickFiles).toHaveBeenCalledTimes(1)
  })

  it('stays enabled — and shows a Detecting/Stop row — while a background detection queue runs', async () => {
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
        return Promise.resolve({
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
            ],
          }),
        })
      }
      if (url.endsWith('/beats/detect')) return detectPromise
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(Sidebar)

    await fireEvent.click(screen.getByTestId('sidebar-import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-detecting')).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByTestId('sidebar-import-files-button')).not.toBeDisabled()
    })

    resolveDetect!({
      ok: true,
      json: async () => ({ status: 'ok', beats: [], count: 5, mean_hr: 60, duration: 10, elapsed_seconds: 0.2, file_size_bytes: 100, error: null }),
    })

    await waitFor(() => {
      expect(screen.queryByTestId('sidebar-detecting')).not.toBeInTheDocument()
    })
  })
})

describe('Sidebar resize', () => {
  it('reports a new width via onResize as the handle is dragged', () => {
    const onResize = vi.fn()
    const { container } = render(Sidebar, { props: { onResize } })

    const aside = container.querySelector('[data-testid="sidebar"]') as HTMLElement
    Object.defineProperty(aside, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ width: 240, left: 0, right: 240, top: 0, bottom: 0, height: 0, x: 0, y: 0, toJSON() {} }),
    })

    const handle = screen.getByTestId('sidebar-resize-handle')
    fireEvent.mouseDown(handle, { clientX: 100 })
    fireEvent.mouseMove(document, { clientX: 140 })

    expect(onResize).toHaveBeenCalledWith(280)

    fireEvent.mouseUp(document)
    fireEvent.mouseMove(document, { clientX: 200 })
    // No further calls after mouseup — the drag listeners were removed.
    expect(onResize).toHaveBeenCalledTimes(1)
  })

  it('hides the resize handle when collapsed', () => {
    render(Sidebar, { props: { collapsed: true } })
    expect(screen.queryByTestId('sidebar-resize-handle')).not.toBeInTheDocument()
  })
})

describe('Sidebar recent files section', () => {
  afterEach(() => {
    autoOpenRequest.value = null
  })

  it('is collapsed by default and toggles open on click', async () => {
    recordRecentFile('/data/57.txt', '57.txt')

    render(Sidebar)

    expect(screen.queryByTestId('sidebar-recent-list')).not.toBeInTheDocument()

    await fireEvent.click(screen.getByTestId('recent-files-toggle'))
    expect(screen.getByTestId('sidebar-recent-list')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-recent-file')).toHaveTextContent('57.txt')

    await fireEvent.click(screen.getByTestId('recent-files-toggle'))
    expect(screen.queryByTestId('sidebar-recent-list')).not.toBeInTheDocument()
  })

  it('omits a recent file that is already in the main (this-session) list', async () => {
    recordRecentFile('/data/57.txt', '57.txt')
    pushRow({ path: '/data/57.txt', filename: '57.txt' })

    render(Sidebar)
    await fireEvent.click(screen.getByTestId('recent-files-toggle'))

    expect(screen.getByText('No recent files')).toBeInTheDocument()
  })

  // A recent file NOT already in this session's registry (the only kind
  // this section ever shows — see the filter test above) goes through
  // openRecentFile's real-import path when clicked; a successful import
  // sets autoOpenRequest the same way a fresh Import Files click does.
  it('imports and sets autoOpenRequest when a not-yet-loaded recent file is clicked', async () => {
    ;(window as any).api = { pickFiles: vi.fn(), pickFolder: vi.fn() }
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
            size: 100,
            modified_time: 1.0,
            default_channel: 'channel 1',
            default_channel_matched_rule: true,
            error: null,
          },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    recordRecentFile('/data/57.txt', '57.txt')

    render(Sidebar)
    await fireEvent.click(screen.getByTestId('recent-files-toggle'))
    await fireEvent.click(screen.getByTestId('sidebar-recent-file'))

    await waitFor(() => {
      expect(autoOpenRequest.value).toEqual({
        path: '/data/57.txt',
        channels: ['channel 1'],
        defaultChannel: 'channel 1',
      })
    })
  })
})

describe('Sidebar clear files', () => {
  it('empties the session file list and tells the shell to leave the review view', async () => {
    fileRegistry.push({
      path: '/data/57.txt',
      filename: '57.txt',
      status: 'detected',
      channels: ['channel 1'],
      defaultChannel: 'channel 1',
      size: 100,
      error: null,
      beatCount: 15,
      meanHr: 72,
    })
    const onFilesCleared = vi.fn()

    render(Sidebar, { props: { onFilesCleared } })
    expect(screen.getAllByTestId('file-row')).toHaveLength(1)

    await fireEvent.click(screen.getByTestId('sidebar-clear-files-button'))

    expect(fileRegistry).toHaveLength(0)
    expect(screen.queryAllByTestId('file-row')).toHaveLength(0)
    expect(onFilesCleared).toHaveBeenCalled()
  })

  it('offers nothing to clear when no files are imported', () => {
    render(Sidebar, { props: {} })

    expect(screen.queryByTestId('sidebar-clear-files-button')).not.toBeInTheDocument()
  })
})
