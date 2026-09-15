import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ReviewWorkspace from './ReviewWorkspace.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

function beatsOkResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: async () => ({
      status: 'ok',
      beats: [],
      count: 42,
      mean_hr: 72,
      duration: 60,
      elapsed_seconds: 0.3,
      file_size_bytes: 1000,
      error: null,
      ...overrides,
    }),
  }
}

describe('ReviewWorkspace', () => {
  it('defaults the channel select to the file default channel', () => {
    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
    })

    const select = screen.getByTestId('channel-select') as HTMLSelectElement
    expect(select.value).toBe('channel 1')
  })

  it('re-runs detection when the channel changes, and shows the updated summary', async () => {
    const fetchMock = vi.fn().mockResolvedValue(beatsOkResponse({ count: 42, mean_hr: 72, duration: 60 }))
    vi.stubGlobal('fetch', fetchMock)

    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
    })

    const select = screen.getByTestId('channel-select') as HTMLSelectElement
    await fireEvent.change(select, { target: { value: 'channel 2' } })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://127.0.0.1:8000/beats/detect',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 2' }),
        }),
      )
    })

    await waitFor(() => {
      const summary = screen.getByTestId('detection-summary')
      expect(summary).toHaveTextContent('42')
      expect(summary).toHaveTextContent('72')
      expect(summary).toHaveTextContent('60')
    })
  })

  it('shows a detection-error summary when detection fails', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'error',
        beats: [],
        count: 0,
        mean_hr: null,
        duration: null,
        elapsed_seconds: 0,
        file_size_bytes: null,
        error: 'boom',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
    })

    const select = screen.getByTestId('channel-select') as HTMLSelectElement
    await fireEvent.change(select, { target: { value: 'channel 2' } })

    await waitFor(() => {
      expect(screen.getByTestId('detection-summary')).toHaveTextContent('boom')
    })
  })
})
