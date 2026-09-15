import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.svelte'
import { resetForTesting } from './lib/stores/eta'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
  resetForTesting()
})

// Exercised end to end (real import flow through the rendered ImportScreen,
// mocking only window.api and fetch) rather than by reaching into App's
// internal view-state, since ImportScreen's Review button is only reachable
// after a real import and that path is already exercised elsewhere the same
// way (see ImportScreen.test.ts) — reusing it here keeps this test honest
// about the actual user flow instead of asserting on implementation details.
describe('App', () => {
  it('switches to Review when a file is reviewed, and back to Import on "Back to Import"', async () => {
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
            channels: ['channel 1', 'channel 2'],
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

    render(App)

    // Import mechanics aren't under test here; skip auto-run detection so
    // the row reaches 'ready' (with a Review button) without a second fetch.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('review-button')).toBeInTheDocument()
    })

    await fireEvent.click(screen.getByTestId('review-button'))

    await waitFor(() => {
      expect(screen.getByTestId('review-workspace')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('import-files-button')).not.toBeInTheDocument()

    await fireEvent.click(screen.getByTestId('back-to-import-button'))

    await waitFor(() => {
      expect(screen.getByTestId('import-files-button')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('review-workspace')).not.toBeInTheDocument()
  })
})
