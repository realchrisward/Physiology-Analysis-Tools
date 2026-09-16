import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ReviewWorkspace from './ReviewWorkspace.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

// jsdom performs no layout, so every element's real `clientWidth` is 0
// unless overridden — same technique as EcgGraph.test.ts's own helper of the
// same name (needed here too, for the click-to-select test below, since a
// zero-width chart has no meaningful pixel-to-data mapping to click into).
function withMockedClientWidth<T>(width: number, fn: () => T): T {
  const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: width })
  try {
    return fn()
  } finally {
    if (original) {
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', original)
    } else {
      delete (HTMLElement.prototype as any).clientWidth
    }
  }
}

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

  // Confirms EcgGraph's `onBeatSelect` prop is actually wired to
  // `selectedBeat` here (Task 1's contract), not just declared. See
  // EcgGraph.test.ts for thorough click-vs-drag/hit-testing coverage of the
  // click gesture itself — this only needs to prove ReviewWorkspace reacts
  // to a call, so it drives the click through the same real gesture rather
  // than reaching into EcgGraph's internals.
  it('shows the selected beat after a click on its marker, via the wired onBeatSelect prop', async () => {
    const targetBeat = {
      // channelWindowOkResponse's x fixture is [0, 1, 2] — placing the beat
      // at the domain midpoint keeps the click math simple (see below).
      ts: 1,
      rr: 0.8,
      r_amplitude: 6,
      hr: 75,
      bradycardia_absolute: false,
      tachycardia_absolute: false,
      skipped_beat: false,
      prem_beat: false,
      abn_cluster: false,
      any_arrhythmia: true,
      other_arrhythmia: false,
    }
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
      if (url.includes('/beats/window')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: 'ok', beats: [targetBeat], count: 1, error: null }),
        })
      }
      return Promise.resolve(beatsOkResponse())
    })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })
    })

    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))

    expect(screen.getByTestId('selected-beat-summary')).toHaveTextContent('No beat selected')

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    // uPlot auto-ranges the x-domain to exactly the merged data's [min, max]
    // with no padding (see EcgGraph.test.ts's `plotWidthPx`/`clientXForVal`
    // comment for the full reasoning) — here that's exactly [0, 2], matching
    // channelWindowOkResponse's x fixture, so this maps a click precisely
    // onto the target beat's ts=1 (the domain midpoint).
    const plotWidthPx = parseFloat(over.style.width)
    const clickX = plotWidthPx * ((1 - 0) / (2 - 0))

    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: clickX, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: clickX, clientY: 100, bubbles: true }))

    await waitFor(() => {
      expect(screen.getByTestId('selected-beat-summary')).toHaveTextContent('t=1s, HR 75')
    })
  })

  // Task 2's pulled-forward persistence prerequisite: `PATCH
  // /files/beats/category` 404s until `POST /files/beats` has succeeded for
  // this (path, channel) — see ReviewWorkspace.svelte's `ensureChannelPersisted`
  // comment for why this fires eagerly on first selection rather than
  // lazily from inside BeatCategoryPanel.
  describe('persist-on-first-selection prerequisite', () => {
    const targetBeat = {
      ts: 1,
      rr: 0.8,
      r_amplitude: 6,
      hr: 75,
      bradycardia_absolute: false,
      tachycardia_absolute: false,
      skipped_beat: false,
      prem_beat: false,
      abn_cluster: false,
      any_arrhythmia: true,
      other_arrhythmia: false,
    }

    // Clicks the beat marker placed at the domain midpoint by
    // channelWindowOkResponse's [0, 1, 2] x fixture — same math as the
    // click-to-select test above.
    function clickBeatMarker() {
      const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
      const plotWidthPx = parseFloat(over.style.width)
      const clickX = plotWidthPx * ((1 - 0) / (2 - 0))
      over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: clickX, clientY: 100, bubbles: true }))
      document.dispatchEvent(new MouseEvent('mouseup', { clientX: clickX, clientY: 100, bubbles: true }))
    }

    function mockFetchWithBeatsWindowAndPersist(persistResponse: () => { ok: boolean; json: () => Promise<unknown> }) {
      return vi.fn().mockImplementation((url: string) => {
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/window')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [targetBeat], count: 1, error: null }),
          })
        }
        if (url.includes('/files/beats')) return Promise.resolve(persistResponse())
        return Promise.resolve(beatsOkResponse())
      })
    }

    it('calls POST /files/beats on first selection, then mounts the category panel, and does not re-persist on a second selection', async () => {
      const fetchMock = mockFetchWithBeatsWindowAndPersist(() => ({
        ok: true,
        json: async () => ({ status: 'ok', count: 1, error: null }),
      }))
      vi.stubGlobal('fetch', fetchMock)

      withMockedClientWidth(800, () => {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))

      clickBeatMarker()

      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'http://127.0.0.1:8000/files/beats',
          expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1' }),
          }),
        )
      })
      await waitFor(() => {
        expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
      })

      const persistCallsAfterFirstSelect = fetchMock.mock.calls.filter((call) =>
        String(call[0]).includes('/files/beats'),
      ).length
      expect(persistCallsAfterFirstSelect).toBe(1)

      clickBeatMarker()
      await waitFor(() => {
        expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
      })

      const persistCallsAfterSecondSelect = fetchMock.mock.calls.filter((call) =>
        String(call[0]).includes('/files/beats'),
      ).length
      expect(persistCallsAfterSecondSelect).toBe(1)
    })

    it('shows a retryable error and withholds the category panel when POST /files/beats fails', async () => {
      let attempt = 0
      const fetchMock = mockFetchWithBeatsWindowAndPersist(() => {
        attempt += 1
        if (attempt === 1) {
          return { ok: true, json: async () => ({ status: 'error', count: 0, error: 'db is locked' }) }
        }
        return { ok: true, json: async () => ({ status: 'ok', count: 1, error: null }) }
      })
      vi.stubGlobal('fetch', fetchMock)

      withMockedClientWidth(800, () => {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))

      clickBeatMarker()

      await waitFor(() => {
        expect(screen.getByTestId('persist-error')).toHaveTextContent('db is locked')
      })
      expect(screen.queryByTestId('beat-category-panel')).not.toBeInTheDocument()

      await fireEvent.click(screen.getByTestId('persist-retry-button'))

      await waitFor(() => {
        expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('persist-error')).not.toBeInTheDocument()
    })
  })

  // Regression coverage for two compounding bugs found in review of Task 2
  // (commit 15648ed): (1) `selected-beat-panel` is a SIBLING of the
  // `{#key activeChannel}`-keyed <EcgGraph>, not nested inside it, so a
  // channel switch alone didn't clear `selectedBeat`/`persistPending`/
  // `persistError` — the category panel kept showing the previous channel's
  // beat as if it belonged to the new one. (2) `POST /files/beats` →
  // `db.replace_beats()` deletes ALL of a file's persisted beat rows (no
  // `channel` column exists) before reinserting for one channel — so the
  // backend can only ever have ONE channel's beats persisted at a time, and
  // a per-channel `Set` tracking "ever persisted" was wrong: switching back
  // to an earlier channel must trigger a fresh persist, not skip it.
  describe('channel-switch resets stale selection and re-persist tracking', () => {
    const beatOnChannel1 = {
      ts: 1,
      rr: 0.8,
      r_amplitude: 6,
      hr: 75,
      bradycardia_absolute: false,
      tachycardia_absolute: false,
      skipped_beat: false,
      prem_beat: false,
      abn_cluster: false,
      any_arrhythmia: true,
      other_arrhythmia: false,
    }
    const beatOnChannel2 = { ...beatOnChannel1, hr: 90 }

    // Clicks the beat marker placed at the domain midpoint by
    // channelWindowOkResponse's [0, 1, 2] x fixture — same math as the
    // click-to-select tests above. Unlike the single-mount tests elsewhere
    // in this file, this describe block's tests remount EcgGraph (via
    // channel switches) and click again afterwards — `data-channel` on the
    // outer wrapper updates from the `channel` prop immediately, but the
    // uPlot chart (and its `.u-over` hit-testing layer) is only constructed
    // once EcgGraph's onMount fetches resolve, slightly later — so this
    // waits for `.u-over` to actually exist rather than assuming it does.
    async function clickBeatMarker() {
      const over = await waitFor(() => {
        const el = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement | null
        if (!el || !el.style.width) throw new Error('chart not ready yet')
        return el
      })
      const plotWidthPx = parseFloat(over.style.width)
      const clickX = plotWidthPx * ((1 - 0) / (2 - 0))
      over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: clickX, clientY: 100, bubbles: true }))
      document.dispatchEvent(new MouseEvent('mouseup', { clientX: clickX, clientY: 100, bubbles: true }))
    }

    // `/beats/window` (GET) carries no `channel` — `getBeatsWindow` only
    // takes path/start/end (see `lib/api/windowing.ts`); the backend's
    // `beat_cache[path]` is keyed only by path, holding whichever channel's
    // `/beats/detect` last completed. So this mock tracks that server-side
    // fact itself: `/beats/detect`'s request body tells us which channel was
    // just (re-)detected, and `/beats/window` replies with that channel's
    // beat — mirroring the real backend, not the frontend's request shape.
    function mockFetchForChannelSwitch() {
      let lastDetectedChannel = 'channel 1'
      return vi.fn().mockImplementation((url: string, init?: { body?: string }) => {
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/detect')) {
          const channel = init?.body ? JSON.parse(init.body).channel : undefined
          if (channel) lastDetectedChannel = channel
          return Promise.resolve(beatsOkResponse())
        }
        if (url.includes('/beats/window')) {
          const beat = lastDetectedChannel === 'channel 2' ? beatOnChannel2 : beatOnChannel1
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [beat], count: 1, error: null }),
          })
        }
        if (url.includes('/files/beats')) {
          return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', count: 1, error: null }) })
        }
        return Promise.resolve(beatsOkResponse())
      })
    }

    it('clears the stale selected beat/category panel when the channel changes with no new click', async () => {
      const fetchMock = mockFetchForChannelSwitch()
      vi.stubGlobal('fetch', fetchMock)

      withMockedClientWidth(800, () => {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))

      await clickBeatMarker()
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'http://127.0.0.1:8000/files/beats',
          expect.objectContaining({
            body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1' }),
          }),
        )
      })
      await waitFor(() => {
        expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
      })

      const select = screen.getByTestId('channel-select') as HTMLSelectElement
      await fireEvent.change(select, { target: { value: 'channel 2' } })

      await waitFor(() => {
        expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
      })

      // The stale channel-1 beat/category panel must be gone — back to the
      // "no beat selected" state — not still showing channel 1's beat.
      expect(screen.queryByTestId('beat-category-panel')).not.toBeInTheDocument()
      expect(screen.getByTestId('selected-beat-summary')).toHaveTextContent('No beat selected')
    })

    it('re-persists a channel that was already persisted earlier, once a different channel has been persisted since', async () => {
      const fetchMock = mockFetchForChannelSwitch()
      vi.stubGlobal('fetch', fetchMock)

      // This test clicks the beat marker across THREE separate EcgGraph
      // mounts (channel 1, then channel 2, then channel 1 again) — unlike
      // the single-mount tests elsewhere in this file, `withMockedClientWidth`
      // scoped to just the initial `render()` call isn't enough, since each
      // channel switch remounts EcgGraph (and bakes in jsdom's real,
      // unmocked `clientWidth` of 0) asynchronously, well after that
      // synchronous scope has already restored the original descriptor. So
      // the override is held for this test's entire body instead.
      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))

        // Select on channel 1 — persists for channel 1.
        await clickBeatMarker()
        await waitFor(() => {
          expect(fetchMock).toHaveBeenCalledWith(
            'http://127.0.0.1:8000/files/beats',
            expect.objectContaining({ body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1' }) }),
          )
        })
        await waitFor(() => expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument())

        // Switch to channel 2, select — persists for channel 2 (destroying
        // channel 1's persisted rows on the real backend).
        const select = screen.getByTestId('channel-select') as HTMLSelectElement
        await fireEvent.change(select, { target: { value: 'channel 2' } })
        await waitFor(() => {
          expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
        })

        await clickBeatMarker()
        await waitFor(() => {
          expect(fetchMock).toHaveBeenCalledWith(
            'http://127.0.0.1:8000/files/beats',
            expect.objectContaining({ body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 2' }) }),
          )
        })
        await waitFor(() => expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument())

        // Switch BACK to channel 1 and select again — must persist AGAIN for
        // channel 1, proving the stale "already persisted" assumption is gone.
        await fireEvent.change(select, { target: { value: 'channel 1' } })
        await waitFor(() => {
          expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
        })

        const persistCallsBeforeReselect = fetchMock.mock.calls.filter((call) =>
          String(call[0]).includes('/files/beats'),
        ).length

        await clickBeatMarker()
        await waitFor(() => {
          const persistCallsAfterReselect = fetchMock.mock.calls.filter((call) =>
            String(call[0]).includes('/files/beats'),
          ).length
          expect(persistCallsAfterReselect).toBe(persistCallsBeforeReselect + 1)
        })
        await waitFor(() => {
          expect(fetchMock).toHaveBeenCalledWith(
            'http://127.0.0.1:8000/files/beats',
            expect.objectContaining({ body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1' }) }),
          )
        })
        await waitFor(() => expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument())
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
    })
  })

  // Regression coverage for the `persistRequestId` guard added alongside
  // this test (mirroring `detectionRequestId`'s existing protection for
  // `detectBeats`, exercised by the "keeps the graph on the previous
  // channel..." test above): `POST /files/beats` requests can complete
  // out of order over a real network even though they're dispatched in
  // order. If the technician selects a beat on channel 1 (dispatching
  // `persistBeats('channel 1')`) then quickly switches to channel 2 and
  // selects a beat there (dispatching `persistBeats('channel 2')`), and
  // channel 1's slower response arrives AFTER channel 2's, the stale
  // channel-1 response must not clobber `lastPersistedChannel` back to
  // `'channel 1'` — the backend (which can only ever hold one channel's
  // persisted beats at a time) actually has channel 2's data persisted.
  describe('persist race: out-of-order POST /files/beats responses', () => {
    const beatOnChannel1 = {
      ts: 1,
      rr: 0.8,
      r_amplitude: 6,
      hr: 75,
      bradycardia_absolute: false,
      tachycardia_absolute: false,
      skipped_beat: false,
      prem_beat: false,
      abn_cluster: false,
      any_arrhythmia: true,
      other_arrhythmia: false,
    }
    const beatOnChannel2 = { ...beatOnChannel1, hr: 90 }

    // Same click-math and "wait for .u-over to be ready" technique as the
    // channel-switch describe block above (this test also remounts EcgGraph
    // across channel switches).
    async function clickBeatMarker() {
      const over = await waitFor(() => {
        const el = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement | null
        if (!el || !el.style.width) throw new Error('chart not ready yet')
        return el
      })
      const plotWidthPx = parseFloat(over.style.width)
      const clickX = plotWidthPx * ((1 - 0) / (2 - 0))
      over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: clickX, clientY: 100, bubbles: true }))
      document.dispatchEvent(new MouseEvent('mouseup', { clientX: clickX, clientY: 100, bubbles: true }))
    }

    it('keeps the later-dispatched channel as the effective last-persisted channel when its response resolves before the earlier channel\'s stale one', async () => {
      let resolvePersistChannel1: ((value: unknown) => void) | undefined
      let resolvePersistChannel2: ((value: unknown) => void) | undefined
      const persistChannel1Promise = new Promise((resolve) => {
        resolvePersistChannel1 = resolve
      })
      const persistChannel2Promise = new Promise((resolve) => {
        resolvePersistChannel2 = resolve
      })

      let lastDetectedChannel = 'channel 1'
      const fetchMock = vi.fn().mockImplementation((url: string, init?: { body?: string }) => {
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/detect')) {
          const channel = init?.body ? JSON.parse(init.body).channel : undefined
          if (channel) lastDetectedChannel = channel
          return Promise.resolve(beatsOkResponse())
        }
        if (url.includes('/beats/window')) {
          const beat = lastDetectedChannel === 'channel 2' ? beatOnChannel2 : beatOnChannel1
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [beat], count: 1, error: null }),
          })
        }
        if (url.includes('/files/beats')) {
          const channel = init?.body ? JSON.parse(init.body).channel : undefined
          if (channel === 'channel 1') return persistChannel1Promise
          if (channel === 'channel 2') return persistChannel2Promise
        }
        return Promise.resolve(beatsOkResponse())
      })
      vi.stubGlobal('fetch', fetchMock)

      // Held for this test's entire body — it remounts EcgGraph across two
      // channel switches (see the identical rationale in the "re-persists a
      // channel that was already persisted earlier..." test above).
      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))

        // Select on channel 1 — dispatches persistBeats('channel 1'), held
        // pending (not yet resolved).
        await clickBeatMarker()
        await waitFor(() => {
          expect(fetchMock).toHaveBeenCalledWith(
            'http://127.0.0.1:8000/files/beats',
            expect.objectContaining({ body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1' }) }),
          )
        })
        await waitFor(() => {
          expect(screen.getByTestId('persist-pending')).toBeInTheDocument()
        })

        // Quickly switch to channel 2 and select a beat there — dispatches
        // persistBeats('channel 2'), also held pending. This is dispatched
        // strictly AFTER channel 1's call above.
        const select = screen.getByTestId('channel-select') as HTMLSelectElement
        await fireEvent.change(select, { target: { value: 'channel 2' } })
        await waitFor(() => {
          expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
        })

        await clickBeatMarker()
        await waitFor(() => {
          expect(fetchMock).toHaveBeenCalledWith(
            'http://127.0.0.1:8000/files/beats',
            expect.objectContaining({ body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 2' }) }),
          )
        })

        // Resolve channel 2's (later-dispatched) response FIRST.
        resolvePersistChannel2!({ ok: true, json: async () => ({ status: 'ok', count: 1, error: null }) })
        await waitFor(() => {
          expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
        })

        // Now resolve channel 1's (earlier-dispatched) response SECOND — an
        // out-of-order completion. Without the `persistRequestId` guard,
        // this stale handler would clobber `lastPersistedChannel` back to
        // 'channel 1'.
        resolvePersistChannel1!({ ok: true, json: async () => ({ status: 'ok', count: 1, error: null }) })
        // Let any (incorrect) stale handling flush before proceeding.
        await Promise.resolve()
        await Promise.resolve()

        // The category panel must still reflect channel 2's beat/state —
        // the stale channel-1 response must not have reset anything.
        expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
        expect(screen.queryByTestId('persist-error')).not.toBeInTheDocument()

        // Switch back to channel 1 and select again. The backend's real
        // last-persisted channel is channel 2 (not channel 1, despite
        // channel 1's response resolving most recently) — so selecting on
        // channel 1 again must trigger a FRESH persistBeats call rather than
        // wrongly skipping it because a stale response set
        // `lastPersistedChannel` back to 'channel 1'.
        await fireEvent.change(select, { target: { value: 'channel 1' } })
        await waitFor(() => {
          expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
        })

        const persistCallsBeforeReselect = fetchMock.mock.calls.filter((call) =>
          String(call[0]).includes('/files/beats'),
        ).length

        await clickBeatMarker()
        await waitFor(() => {
          const persistCallsAfterReselect = fetchMock.mock.calls.filter((call) =>
            String(call[0]).includes('/files/beats'),
          ).length
          expect(persistCallsAfterReselect).toBe(persistCallsBeforeReselect + 1)
        })
        await waitFor(() => {
          expect(fetchMock).toHaveBeenCalledWith(
            'http://127.0.0.1:8000/files/beats',
            expect.objectContaining({ body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1' }) }),
          )
        })
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
    })
  })

  // Task 3: arrhythmia re-run controls. ArrhythmiaControls' own behavior
  // (method buttons, progress feedback, success/failure handling) is covered
  // in ArrhythmiaControls.test.ts — this only proves the two things that are
  // ReviewWorkspace's own responsibility: it's mounted against the current
  // `activeChannel`, and a successful run triggers EcgGraph's beats-ONLY
  // refresh (a fresh `/beats/window` call) without re-fetching
  // `/channels/window` or resetting the viewport.
  describe('arrhythmia re-run wiring', () => {
    function mockFetchForArrhythmiaRerun() {
      return vi.fn().mockImplementation((url: string) => {
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/window')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [], count: 0, error: null }),
          })
        }
        if (url.includes('/arrhythmia/detect')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              status: 'ok',
              beats: [],
              count: 5,
              any_arrhythmia_count: 1,
              elapsed_seconds: 0.2,
              error: null,
            }),
          })
        }
        return Promise.resolve(beatsOkResponse())
      })
    }

    it('mounts ArrhythmiaControls against the active channel and, on a successful run, refreshes only the graph beat markers', async () => {
      const fetchMock = mockFetchForArrhythmiaRerun()
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      // Initial mount: /channels/window + /beats/window.
      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))

      await fireEvent.click(screen.getByTestId('run-heuristic-button'))

      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'http://127.0.0.1:8000/arrhythmia/detect',
          expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1', method: 'heuristic' }),
          }),
        )
      })

      // The successful run's onComplete bumps EcgGraph's `beatsRefreshToken`
      // prop, which re-fetches ONLY `/beats/window` at the current visible
      // range — no second `/channels/window` call (that would mean the
      // waveform/viewport was needlessly reset).
      await waitFor(() => {
        const calledPaths = fetchMock.mock.calls.map((call) => new URL(String(call[0])).pathname)
        expect(calledPaths.filter((p) => p === '/beats/window')).toHaveLength(2)
        expect(calledPaths.filter((p) => p === '/channels/window')).toHaveLength(1)
      })
    })

    // Regression coverage for the corruption exploit this fix closes:
    // `ArrhythmiaControls` is mounted against `activeChannel` as a live prop
    // (NOT `{#key activeChannel}`-wrapped), so a slow `detectArrhythmias`
    // run started on channel 1 stays in flight across a channel switch. If
    // its stale response were allowed to call `onComplete` after channel 2
    // became active, it would bump `beatsRefreshToken` and trigger an extra
    // `/beats/window` fetch against `beat_cache[path]` — which, on a real
    // backend, channel 1's own (now-late) `/arrhythmia/detect` response
    // would have just clobbered back to channel 1's data, since the cache is
    // keyed only by path (see `backend/beats.py`). This test can't reproduce
    // that backend clobbering with mocked fetches, but it proves the
    // frontend half unconditionally required to prevent it: the stale
    // channel-1 completion must never fire a second `/beats/window` fetch
    // once channel 2 is active. Same "hold the promise pending, resolve out
    // of order" technique as the "persist race" describe block above.
    it('does not refresh the graph when a stale arrhythmia rerun for a previous channel resolves after switching channels', async () => {
      let resolveArrhythmia: ((value: unknown) => void) | undefined
      const arrhythmiaPromise = new Promise((resolve) => {
        resolveArrhythmia = resolve
      })

      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/window')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [], count: 0, error: null }),
          })
        }
        if (url.includes('/arrhythmia/detect')) return arrhythmiaPromise
        return Promise.resolve(beatsOkResponse()) // /beats/detect
      })
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      // Initial mount: /channels/window + /beats/window for channel 1.
      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(2))
      expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')

      // Start an arrhythmia rerun on channel 1 — held pending, not resolved.
      await fireEvent.click(screen.getByTestId('run-heuristic-button'))
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'http://127.0.0.1:8000/arrhythmia/detect',
          expect.objectContaining({
            body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1', method: 'heuristic' }),
          }),
        )
      })

      // Before it resolves, switch to channel 2 — its own detectBeats
      // resolves normally (mockFetch's default /beats/detect branch), so
      // the graph advances to channel 2.
      const select = screen.getByTestId('channel-select') as HTMLSelectElement
      await fireEvent.change(select, { target: { value: 'channel 2' } })

      await waitFor(() => {
        expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
      })

      const beatsWindowCallsBeforeStaleResolve = fetchMock.mock.calls.filter((call) =>
        new URL(String(call[0])).pathname === '/beats/window',
      ).length

      // Now resolve channel 1's stale arrhythmia response.
      resolveArrhythmia!({
        ok: true,
        json: async () => ({
          status: 'ok',
          beats: [],
          count: 5,
          any_arrhythmia_count: 1,
          elapsed_seconds: 0.2,
          error: null,
        }),
      })
      // Let any (incorrect) stale onComplete/refresh flush before asserting.
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()

      // The graph must still be on channel 2, and must NOT have fired an
      // extra /beats/window fetch in response to the stale channel-1 run.
      expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
      const beatsWindowCallsAfterStaleResolve = fetchMock.mock.calls.filter((call) =>
        new URL(String(call[0])).pathname === '/beats/window',
      ).length
      expect(beatsWindowCallsAfterStaleResolve).toBe(beatsWindowCallsBeforeStaleResolve)
    })
  })
})
