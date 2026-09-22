import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetForTesting as resetTheme } from '../../lib/stores/theme.svelte'
import SettingsDialog from './SettingsDialog.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
  resetTheme()
})

// Real, already-verified defaults from BACKEND_OVERVIEW.md — used so this
// test exercises the actual shape/values the backend returns, not made-up
// numbers.
function settingsPayload(overrides: { beat?: object; arrhythmia?: object } = {}) {
  return {
    beat: {
      min_RR: 60,
      ecg_invert: false,
      auto_detect_invert: true,
      ecg_filter: true,
      ecg_filt_order: 4,
      ecg_filt_cutoff: 25,
      abs_thresh: null,
      perc_thresh: 97,
      ...overrides.beat,
    },
    arrhythmia: {
      bradycardia_absolute_hr: 300,
      tachycardia_absolute_hr: 850,
      skipped_beat_multiple_rr: 1.8,
      premature_beat_multiple_rr: 0.8,
      beat_window: 1,
      beat_window_bias: 0,
      beat_length: 128,
      kde_bandwidth: 0.05,
      min_rr: 0.1,
      max_rr: 0.1667,
      eps_auto: true,
      eps_percentile: 90,
      eps: 0.3,
      min_samples: 3,
      ...overrides.arrhythmia,
    },
  }
}

function getSettingsOkResponse(overrides: { beat?: object; arrhythmia?: object } = {}) {
  return {
    ok: true,
    json: async () => settingsPayload(overrides),
  }
}

function mockFetch(putResponse?: { ok: boolean; json: () => Promise<unknown> }) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      return Promise.resolve(
        putResponse ?? {
          ok: true,
          json: async () => ({ status: 'ok', settings: settingsPayload(), error: null }),
        },
      )
    }
    return Promise.resolve(getSettingsOkResponse())
  })
}

describe('SettingsDialog', () => {
  it('loads and displays current settings on open', async () => {
    vi.stubGlobal('fetch', mockFetch())

    render(SettingsDialog, { props: { onClose: vi.fn() } })

    await waitFor(() => {
      expect((screen.getByTestId('beat-min-rr-input') as HTMLInputElement).value).toBe('60')
    })

    expect((screen.getByTestId('beat-perc-thresh-input') as HTMLInputElement).value).toBe('97')
    expect((screen.getByTestId('arrhythmia-bradycardia-absolute-hr-input') as HTMLInputElement).value).toBe('300')
    expect((screen.getByTestId('arrhythmia-tachycardia-absolute-hr-input') as HTMLInputElement).value).toBe('850')
    expect((screen.getByTestId('arrhythmia-beat-length-input') as HTMLInputElement).value).toBe('128')
    expect((screen.getByTestId('arrhythmia-max-rr-input') as HTMLInputElement).value).toBe('0.1667')
    // eps_auto is on, so the adaptive percentile field shows in place of the
    // fixed cluster-radius one.
    expect((screen.getByTestId('arrhythmia-eps-percentile-input') as HTMLInputElement).value).toBe('90')
    expect(screen.queryByTestId('arrhythmia-eps-input')).not.toBeInTheDocument()
  })

  it('calls putSettings with the full edited payload on save', async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal('fetch', fetchMock)

    render(SettingsDialog, { props: { onClose: vi.fn() } })

    await waitFor(() => {
      expect((screen.getByTestId('beat-min-rr-input') as HTMLInputElement).value).toBe('60')
    })

    await fireEvent.input(screen.getByTestId('arrhythmia-bradycardia-absolute-hr-input'), {
      target: { value: '250' },
    })

    await fireEvent.click(screen.getByTestId('save-settings-button'))

    function findPutCall() {
      return fetchMock.mock.calls.find((call) => (call[1] as RequestInit | undefined)?.method === 'PUT')
    }

    await waitFor(() => {
      expect(findPutCall()).toBeTruthy()
    })

    const putCall = findPutCall()!
    const body = JSON.parse((putCall[1] as RequestInit).body as string)

    expect(body).toEqual(
      settingsPayload({
        arrhythmia: { bradycardia_absolute_hr: 250 },
      }),
    )
  })

  it('surfaces a validation error from the backend inline and keeps the dialog open', async () => {
    const fetchMock = mockFetch({
      ok: true,
      json: async () => ({
        status: 'error',
        settings: null,
        error: 'bradycardia_absolute_hr must be less than tachycardia_absolute_hr',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const onClose = vi.fn()

    render(SettingsDialog, { props: { onClose } })

    await waitFor(() => {
      expect((screen.getByTestId('beat-min-rr-input') as HTMLInputElement).value).toBe('60')
    })

    await fireEvent.click(screen.getByTestId('save-settings-button'))

    await waitFor(() => {
      expect(screen.getByTestId('settings-error')).toHaveTextContent(
        'bradycardia_absolute_hr must be less than tachycardia_absolute_hr',
      )
    })

    expect(screen.getByTestId('settings-dialog')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes the dialog on a successful save', async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal('fetch', fetchMock)
    const onClose = vi.fn()

    render(SettingsDialog, { props: { onClose } })

    await waitFor(() => {
      expect((screen.getByTestId('beat-min-rr-input') as HTMLInputElement).value).toBe('60')
    })

    await fireEvent.click(screen.getByTestId('save-settings-button'))

    await waitFor(() => {
      expect(onClose).toHaveBeenCalled()
    })
  })

  it('dismisses via the close (×) button', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const onClose = vi.fn()

    render(SettingsDialog, { props: { onClose } })

    await waitFor(() => {
      expect((screen.getByTestId('beat-min-rr-input') as HTMLInputElement).value).toBe('60')
    })

    await fireEvent.click(screen.getByTestId('close-settings-button'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('dismisses when the backdrop itself is clicked, but not when the card is clicked', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const onClose = vi.fn()

    render(SettingsDialog, { props: { onClose } })

    await waitFor(() => {
      expect((screen.getByTestId('beat-min-rr-input') as HTMLInputElement).value).toBe('60')
    })

    await fireEvent.click(screen.getByTestId('settings-dialog'))
    expect(onClose).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByTestId('settings-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('shows the Appearance theme options and applies a selection immediately', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const { themeState } = await import('../../lib/stores/theme.svelte')

    render(SettingsDialog, { props: { onClose: vi.fn() } })

    expect(screen.getByTestId('theme-system-button').getAttribute('aria-pressed')).toBe('true')

    await fireEvent.click(screen.getByTestId('theme-bw-button'))

    expect(themeState.mode).toBe('bw')
    expect(screen.getByTestId('theme-bw-button').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('theme-system-button').getAttribute('aria-pressed')).toBe('false')
  })

  it('dismisses on Escape', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const onClose = vi.fn()

    render(SettingsDialog, { props: { onClose } })

    await waitFor(() => {
      expect((screen.getByTestId('beat-min-rr-input') as HTMLInputElement).value).toBe('60')
    })

    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('SettingsDialog restore defaults', () => {
  it('fills the form from the backend defaults without saving them', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: 'ok', settings: settingsPayload(), error: null }),
        })
      }
      if (url.includes('/settings/defaults')) {
        return Promise.resolve(getSettingsOkResponse({ arrhythmia: { bradycardia_absolute_hr: 300 } }))
      }
      return Promise.resolve(getSettingsOkResponse({ arrhythmia: { bradycardia_absolute_hr: 111 } }))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(SettingsDialog, { props: { onClose: vi.fn() } })

    const brady = () => screen.getByTestId('arrhythmia-bradycardia-absolute-hr-input') as HTMLInputElement
    await waitFor(() => expect(brady().value).toBe('111'))

    await fireEvent.click(screen.getByTestId('restore-defaults-button'))

    await waitFor(() => expect(brady().value).toBe('300'))
    // Restoring only repopulates the form — nothing is applied until Save.
    expect(fetchMock.mock.calls.some((call) => (call[1] as RequestInit | undefined)?.method === 'PUT')).toBe(false)
  })
})
