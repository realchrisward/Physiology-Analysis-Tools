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

function channelWindowOkResponse() {
  return {
    ok: true,
    json: async () => ({
      status: 'ok',
      x: [0, 1, 2],
      y: [0, 1, 0],
      point_count: 3,
      downsampled: false,
      error: null,
    }),
  }
}

// The mounted EcgGraph (see ecg-graph tests for its own coverage) fetches
// `/channels/window` on mount independently of whatever `/beats/detect`
// behavior a given test is exercising — so every fetch mock here has to
// answer both endpoints, not just the one the test cares about.
function mockFetch(beatsResponse: { ok: boolean; json: () => Promise<unknown> }) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
    return Promise.resolve(beatsResponse)
  })
}

describe('ReviewWorkspace', () => {
  it('defaults the channel select to the file default channel', () => {
    // EcgGraph fetches `/channels/window` unconditionally on mount; stub it
    // so this test doesn't attempt a real network call.
    vi.stubGlobal('fetch', mockFetch(beatsOkResponse()))

    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
    })

    const select = screen.getByTestId('channel-select') as HTMLSelectElement
    expect(select.value).toBe('channel 1')
  })

  it('re-runs detection when the channel changes, and shows the updated summary', async () => {
    const fetchMock = mockFetch(beatsOkResponse({ count: 42, mean_hr: 72, duration: 60 }))
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
    const fetchMock = mockFetch({
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

  // Regression coverage for the channel-switch race: EcgGraph's own
  // onMount fetch (`/channels/window` then `/beats/window`) is a cheap
  // dataframe filter that, against a real backend, resolves far faster
  // than `/beats/detect`'s real peak-detection computation — and
  // `beat_cache[path]` on the backend is keyed only by path, overwritten by
  // whichever channel's detection last completed. If EcgGraph were keyed
  // on `selectedChannel` directly (the pre-fix behavior), it would remount
  // for the new channel immediately, well before detection for that channel
  // has actually run — rendering the previous channel's stale markers (or
  // none) over the new channel's waveform. These tests can't reproduce a
  // real network race with deterministic mocked fetches, so instead they
  // control the `/beats/detect` promise's resolution directly to prove the
  // component *waits* for it before ever mounting EcgGraph against the new
  // channel.
  it('keeps the graph on the previous channel while detection for the new selection is in flight, then switches once it resolves', async () => {
    let resolveDetect: ((value: unknown) => void) | undefined
    const detectPromise = new Promise((resolve) => {
      resolveDetect = resolve
    })

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/beats/detect')) return detectPromise
      if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
      return Promise.resolve(beatsOkResponse()) // /beats/window
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
    })

    // Wait for the initial mount's own channel-window + beats-window fetch
    // pair to settle before capturing the "before switch" call count, so it
    // doesn't race the assertion below.
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))
    expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
    const fetchCallsBeforeSwitch = fetchMock.mock.calls.length

    const select = screen.getByTestId('channel-select') as HTMLSelectElement
    await fireEvent.change(select, { target: { value: 'channel 2' } })

    // The dropdown reflects the choice immediately...
    expect(select.value).toBe('channel 2')
    // ...the in-flight-detection state is visible...
    await waitFor(() => {
      expect(screen.getByTestId('detection-summary')).toHaveTextContent('channel 2')
    })
    // ...but the graph must NOT have switched yet: still keyed to the
    // previous channel, and no new fetch has fired for the new one
    // (detectBeats is the only call so far; EcgGraph hasn't remounted).
    expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
    expect(fetchMock.mock.calls.length).toBe(fetchCallsBeforeSwitch + 1)

    // Now let detectBeats resolve successfully for channel 2.
    resolveDetect!(beatsOkResponse({ count: 10, mean_hr: 80, duration: 30 }))

    await waitFor(() => {
      expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
    })
    // The graph's remount fires new /channels/window + /beats/window calls
    // for the new channel.
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBe(fetchCallsBeforeSwitch + 3)
    })
  })

  it('does not advance the graph to the new channel when detection for it fails', async () => {
    let resolveDetect: ((value: unknown) => void) | undefined
    const detectPromise = new Promise((resolve) => {
      resolveDetect = resolve
    })

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/beats/detect')) return detectPromise
      if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
      return Promise.resolve(beatsOkResponse()) // /beats/window
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
    })

    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))
    expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
    const fetchCallsBeforeSwitch = fetchMock.mock.calls.length

    const select = screen.getByTestId('channel-select') as HTMLSelectElement
    await fireEvent.change(select, { target: { value: 'channel 2' } })

    resolveDetect!({
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

    await waitFor(() => {
      expect(screen.getByTestId('detection-summary')).toHaveTextContent('boom')
    })

    // The graph must stay on the last-known-good channel — never advance to
    // the one detection just failed for — and no new window/beats fetches
    // for channel 2 should have fired.
    expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
    expect(fetchMock.mock.calls.length).toBe(fetchCallsBeforeSwitch + 1)
  })
})
