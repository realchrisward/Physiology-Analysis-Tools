import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.svelte'
import { resetForTesting as resetEta } from './lib/stores/eta'
import { resetForTesting as resetFileRegistry } from './lib/stores/fileRegistry.svelte'
import { resetForTesting as resetImportQueue } from './lib/stores/importQueue.svelte'
import { resetForTesting as resetRecentFiles } from './lib/stores/recentFiles.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
  resetEta()
  resetFileRegistry()
  resetImportQueue()
  resetRecentFiles()
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

  it('opens the first imported file in Review automatically, with no explicit Open click', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/57.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/files/import')) {
        return Promise.resolve({
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
      }
      // ReviewWorkspace's own mount-time hydration/EcgGraph fetches — a
      // generic "found: false" / empty-ok shape satisfies all of them for
      // the purposes of this test, which only cares about navigation.
      return Promise.resolve({
        ok: true,
        json: async () => ({ status: 'ok', found: false, x: [], y: [], beats: [], error: null }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(App)

    // Auto-run off: this test is about navigation, not detection.
    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('review-workspace')).toBeInTheDocument()
    })
    expect(screen.getByTestId('breadcrumb-current')).toHaveTextContent('57.txt')
  })

  // Settings must be reachable from both views, since detection parameters
  // matter whether the technician is about to import/detect or is already
  // reviewing a file. These are smoke tests (button present, dialog opens) —
  // SettingsDialog's own behavior (load/save/validation-error) is covered in
  // SettingsDialog.test.ts.
  it('shows the settings button in the Import view and opens the dialog', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'ok',
        beat: { min_RR: 60, ecg_invert: false, auto_detect_invert: true, ecg_filter: true, ecg_filt_order: 4, ecg_filt_cutoff: 25, abs_thresh: null, perc_thresh: 97 },
        arrhythmia: { bradycardia_absolute_hr: 300, tachycardia_absolute_hr: 850, skipped_beat_multiple_rr: 1.8, premature_beat_multiple_rr: 0.8, window_size: 100, eps: 0.3, min_samples: 3 },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(App)

    expect(screen.getByTestId('import-files-button')).toBeInTheDocument()
    await fireEvent.click(screen.getByTestId('settings-button'))

    await waitFor(() => {
      expect(screen.getByTestId('settings-dialog')).toBeInTheDocument()
    })
  })

  it('shows the settings button in the Review view and opens the dialog', async () => {
    ;(window as any).api = {
      pickFiles: vi.fn().mockResolvedValue(['/data/57.txt']),
      pickFolder: vi.fn().mockResolvedValue([]),
    }
    // A single fetch mock stands in for every backend endpoint hit along the
    // way (import, ReviewWorkspace's mount-time /files/state hydration,
    // EcgGraph's /channels/window) as well as /settings once the dialog is
    // opened. This test only cares that the settings button/dialog work once
    // Review is showing, not about exercising each endpoint's real shape —
    // matching the existing "switches to Review" test's approach above.
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/settings')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: 'ok',
            beat: { min_RR: 60, ecg_invert: false, auto_detect_invert: true, ecg_filter: true, ecg_filt_order: 4, ecg_filt_cutoff: 25, abs_thresh: null, perc_thresh: 97 },
            arrhythmia: { bradycardia_absolute_hr: 300, tachycardia_absolute_hr: 850, skipped_beat_multiple_rr: 1.8, premature_beat_multiple_rr: 0.8, window_size: 100, eps: 0.3, min_samples: 3 },
          }),
        })
      }
      return Promise.resolve({
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
    })
    vi.stubGlobal('fetch', fetchMock)

    render(App)

    await fireEvent.click(screen.getByTestId('auto-run-checkbox'))
    await fireEvent.click(screen.getByTestId('import-files-button'))

    await waitFor(() => {
      expect(screen.getByTestId('review-button')).toBeInTheDocument()
    })
    await fireEvent.click(screen.getByTestId('review-button'))

    await waitFor(() => {
      expect(screen.getByTestId('review-workspace')).toBeInTheDocument()
    })

    await fireEvent.click(screen.getByTestId('settings-button'))

    await waitFor(() => {
      expect(screen.getByTestId('settings-dialog')).toBeInTheDocument()
    })
  })
})
