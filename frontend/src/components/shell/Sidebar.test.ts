import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fileRegistry, resetForTesting as resetFileRegistry, type FileRow } from '../../lib/stores/fileRegistry.svelte'
import { resetForTesting as resetImportQueue } from '../../lib/stores/importQueue.svelte'
import { resetForTesting as resetEta } from '../../lib/stores/eta'
import Sidebar from './Sidebar.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
  resetEta()
  resetFileRegistry()
  resetImportQueue()
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

  it('shows a Review button for a row with a channel, firing onReview with path/channels/defaultChannel', async () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt', channels: ['channel 1'], defaultChannel: 'channel 1' })
    const onReview = vi.fn()

    render(Sidebar, { props: { onReview } })

    await fireEvent.click(screen.getByTestId('review-button'))

    expect(onReview).toHaveBeenCalledWith({
      path: '/data/57.txt',
      channels: ['channel 1'],
      defaultChannel: 'channel 1',
    })
  })

  it('shows no Review button for a row that failed to import', () => {
    pushRow({ path: '/data/bad.txt', filename: 'bad.txt', status: 'error', defaultChannel: null, channels: [] })

    render(Sidebar, { props: { onReview: vi.fn() } })

    expect(screen.queryByTestId('review-button')).not.toBeInTheDocument()
  })

  it('highlights the active file and hides row detail when collapsed', () => {
    pushRow({ path: '/data/57.txt', filename: '57.txt' })

    const { container } = render(Sidebar, { props: { collapsed: true, activePath: '/data/57.txt' } })

    // Collapsed mode hides the filename/status/Review button — only the
    // status dot remains, with the filename reachable via the row's title.
    expect(screen.queryByText('57.txt')).not.toBeInTheDocument()
    expect(screen.queryByTestId('review-button')).not.toBeInTheDocument()
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
