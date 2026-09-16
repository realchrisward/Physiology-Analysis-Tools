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
})
