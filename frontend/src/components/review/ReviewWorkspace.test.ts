import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ReviewWorkspace from './ReviewWorkspace.svelte'
import { fileRegistry, resetForTesting as resetFileRegistry } from '../../lib/stores/fileRegistry.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
  resetFileRegistry()
})

// jsdom performs no layout, so every element's real `clientWidth` is 0
// unless overridden — a zero-width chart has no meaningful pixel-to-data
// mapping to click into. Every test below that needs to click a beat marker
// overrides `HTMLElement.prototype.clientWidth` directly (restoring it in a
// `finally`) rather than through a shared helper scoped to just the
// synchronous `render()` call: EcgGraph only mounts once ReviewWorkspace's
// own mount-time hydration (`getFileState`) has settled (see
// `hydrationChecked` in ReviewWorkspace.svelte), which is always at least
// one microtask after `render()` returns — so a helper whose override only
// lives for the duration of a synchronous callback around `render()` would
// already have restored the real (zero) `clientWidth` by the time EcgGraph
// actually reads it in its own `onMount`. Holding the override open for
// each test's full body (or, for tests with no click interaction at all,
// not bothering with it) is what actually works here.

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

// F5 reopen hydration's `GET /files/state`, called once from
// ReviewWorkspace's own `onMount` (see that component). Every test in this
// file now triggers this call at mount, whether or not the test cares about
// its outcome — `found: false` (this default) is the "brand-new file, no
// prior state" case and leaves all of today's pre-F5 behavior untouched, so
// it's a safe default for every test that doesn't explicitly test hydration
// itself.
function fileStateResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: async () => ({
      status: 'ok',
      found: false,
      channel: null,
      beats: [],
      bad_data_marks: [],
      beat_settings: null,
      arrhythmia_settings: null,
      error: null,
      ...overrides,
    }),
  }
}

// The mounted EcgGraph (see ecg-graph tests for its own coverage) fetches
// `/channels/window` on mount independently of whatever `/beats/detect`
// behavior a given test is exercising — so every fetch mock here has to
// answer both endpoints, not just the one the test cares about. Also routes
// `GET /files/state` (ReviewWorkspace's own mount-time hydration check, see
// `fileStateResponse` above) to a "not found" default so hydration never
// overrides `defaultChannel` for tests that aren't exercising it.
function mockFetch(beatsResponse: { ok: boolean; json: () => Promise<unknown> }) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
    if (url.includes('/files/state')) return Promise.resolve(fileStateResponse())
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

  it('re-runs detection for the current channel via the Re-run detection button', async () => {
    const fetchMock = mockFetch(beatsOkResponse({ count: 15, mean_hr: 65, duration: 30 }))
    vi.stubGlobal('fetch', fetchMock)

    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
    })

    await waitFor(() => expect(screen.getByTestId('rerun-detection-button')).not.toBeDisabled())

    await fireEvent.click(screen.getByTestId('rerun-detection-button'))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://127.0.0.1:8000/beats/detect',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1' }),
        }),
      )
    })

    await waitFor(() => {
      const summary = screen.getByTestId('detection-summary')
      expect(summary).toHaveTextContent('15')
      expect(summary).toHaveTextContent('65')
    })
  })

  // A folder import's background auto-run detection (importQueue.svelte.ts)
  // can still be queued, or actively running, by the time a technician
  // opens the file here from the sidebar — this reflects that shared
  // registry row's own status into the toolbar instead of showing a blank
  // summary and empty graph markers until a channel change/rerun.
  it('reflects the shared file registry row (queued → detected) into the detection summary and refreshes the graph', async () => {
    const fetchMock = mockFetch(beatsOkResponse())
    vi.stubGlobal('fetch', fetchMock)

    fileRegistry.push({
      path: '/data/57.txt',
      filename: '57.txt',
      status: 'queued',
      channels: ['channel 1'],
      defaultChannel: 'channel 1',
      size: 100,
      error: null,
      beatCount: null,
      meanHr: null,
    })

    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1'], defaultChannel: 'channel 1' },
    })

    await waitFor(() => {
      expect(screen.getByTestId('detection-summary')).toHaveTextContent('beat detection is queued')
    })

    const callsBeforeDetected = fetchMock.mock.calls.length
    const row = fileRegistry[0]
    row.status = 'detecting'

    await waitFor(() => {
      expect(screen.getByTestId('detection-summary')).toHaveTextContent('detecting beats')
    })

    row.status = 'detected'
    row.beatCount = 15
    row.meanHr = 65

    await waitFor(() => {
      const summary = screen.getByTestId('detection-summary')
      expect(summary).toHaveTextContent('15')
      expect(summary).toHaveTextContent('65')
    })
    // Graph markers were refreshed (a new /beats/window-shaped fetch fired)
    // rather than staying stuck showing the empty pre-detection window.
    expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBeforeDetected)
  })

  it('shows a loading indicator in the graph area while mount-time hydration is still in flight', async () => {
    let resolveFileState: ((value: unknown) => void) | undefined
    const pendingFileState = new Promise((resolve) => {
      resolveFileState = resolve
    })
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
      if (url.includes('/files/state')) return pendingFileState
      return Promise.resolve(beatsOkResponse())
    })
    vi.stubGlobal('fetch', fetchMock)

    render(ReviewWorkspace, {
      props: { path: '/data/57.txt', channels: ['channel 1'], defaultChannel: 'channel 1' },
    })

    expect(screen.getByTestId('graph-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('ecg-graph')).not.toBeInTheDocument()

    resolveFileState!(fileStateResponse())

    await waitFor(() => expect(screen.queryByTestId('graph-loading')).not.toBeInTheDocument())
    expect(screen.getByTestId('ecg-graph')).toBeInTheDocument()
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

    // Wait for the initial mount's own files/state + channel-window + beats-window fetch
    // pair to settle before capturing the "before switch" call count, so it
    // doesn't race the assertion below.
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))
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
    // for the new channel, plus the fire-and-forget `PUT /files/channel`
    // that now follows a successful channel switch (see
    // `handleChannelChange`'s `putChannel` call).
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBe(fetchCallsBeforeSwitch + 4)
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

    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))
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

    // EcgGraph no longer mounts synchronously within `render()` — it waits
    // for ReviewWorkspace's mount-time hydration (`getFileState`) to settle
    // first (see `hydrationChecked` in ReviewWorkspace.svelte), which only
    // happens after an `await` has already let this synchronous test body
    // move on. So the `clientWidth` override has to be held across that gap
    // — scoping it to just the `render()` call (as a single-mount test could
    // get away with before this gate existed) is no longer enough. Same
    // technique the multi-mount describe blocks below already use.
    const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
    try {
      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

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
    } finally {
      if (originalClientWidth) {
        Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
      } else {
        delete (HTMLElement.prototype as any).clientWidth
      }
    }
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

      // See the identical rationale in "shows the selected beat after a
      // click on its marker..." above: EcgGraph only mounts after
      // ReviewWorkspace's mount-time hydration settles, well after
      // `render()` returns, so the `clientWidth` override has to be held
      // for the whole test body, not just the synchronous `render()` call.
      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

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
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
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

      // Same rationale as the sibling test above: hold the override for the
      // whole body since EcgGraph mounts asynchronously, after `render()`
      // returns.
      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

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
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
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

      // EcgGraph mounts only after ReviewWorkspace's mount-time hydration
      // settles (see `hydrationChecked` in ReviewWorkspace.svelte) — well
      // after `render()` returns — and this test also switches channels
      // (remounting EcgGraph again), so the override is held for the whole
      // body, same as the "re-persists a channel..." test below.
      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

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
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
    })

    it('re-persists a channel that was already persisted earlier, once a different channel has been persisted since', async () => {
      const fetchMock = mockFetchForChannelSwitch()
      vi.stubGlobal('fetch', fetchMock)

      // This test clicks the beat marker across THREE separate EcgGraph
      // mounts (channel 1, then channel 2, then channel 1 again), each one
      // asynchronous (see the file-level comment above on why the override
      // has to be held open rather than scoped to a synchronous callback) —
      // so the override is held for this test's entire body.
      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

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

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

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

      // Initial mount: /files/state + /channels/window + /beats/window.
      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

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

      // Initial mount: /files/state + /channels/window + /beats/window for channel 1.
      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))
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

    // The core fix this wave adds: a successful arrhythmia rerun only ever
    // updated the in-memory `beat_cache[path]` on the backend before this —
    // never written back to SQLite — so a report generated after a rerun
    // would silently reflect the STALE pre-rerun detection. `POST
    // /files/beats` is the only thing that persists `beat_cache` to SQLite
    // (via `replace_beats`), so a successful rerun must trigger it again,
    // in addition to the existing beats-marker refresh proven above.
    it('also persists the channel via POST /files/beats after a successful rerun, on top of an earlier beat-selection persist', async () => {
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
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return Promise.resolve(fileStateResponse())
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/window')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [targetBeat], count: 1, error: null }),
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
        if (url.includes('/files/beats/category')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', ts: 1, review_state: 'confirmed', reassigned_category: null, error: null }),
          })
        }
        if (url.includes('/files/beats')) {
          return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', count: 1, error: null }) })
        }
        return Promise.resolve(beatsOkResponse())
      })
      vi.stubGlobal('fetch', fetchMock)

      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

        // Select a beat first — this fires the FIRST `POST /files/beats`
        // via `ensureChannelPersisted`.
        const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
        const plotWidthPx = parseFloat(over.style.width)
        const clickX = plotWidthPx * ((1 - 0) / (2 - 0))
        over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: clickX, clientY: 100, bubbles: true }))
        document.dispatchEvent(new MouseEvent('mouseup', { clientX: clickX, clientY: 100, bubbles: true }))

        await waitFor(() => {
          expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
        })

        const persistCallsAfterSelection = fetchMock.mock.calls.filter(
          (call) => new URL(String(call[0])).pathname === '/files/beats',
        ).length
        expect(persistCallsAfterSelection).toBe(1)

        // Now run arrhythmia detection — a successful rerun must fire a
        // SECOND `/files/beats` call, with the current channel.
        await fireEvent.click(screen.getByTestId('run-heuristic-button'))

        await waitFor(() => {
          const persistCallsAfterRerun = fetchMock.mock.calls.filter(
            (call) => new URL(String(call[0])).pathname === '/files/beats',
          ).length
          expect(persistCallsAfterRerun).toBe(2)
        })

        const persistCalls = fetchMock.mock.calls.filter(
          (call) => new URL(String(call[0])).pathname === '/files/beats',
        )
        expect(persistCalls[1][1]).toEqual(
          expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1' }),
          }),
        )
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
    })
  })

  // Task 2 of F5: reopening a file loads its prior review state (channel,
  // bad-data marks) instead of starting over, via `GET /files/state` in
  // ReviewWorkspace's own `onMount` (see that component). Task 1's backend
  // restore (`df02d2c`) is what makes a persisted channel's `beat_cache`
  // trustworthy enough to skip re-detection entirely here.
  describe('reopen hydration: GET /files/state on mount', () => {
    function mockFetchWithFileState(
      fileState: { ok: boolean; json: () => Promise<unknown> },
      opts: { onDetect?: () => unknown } = {},
    ) {
      return vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return Promise.resolve(fileState)
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/detect')) return Promise.resolve((opts.onDetect ?? (() => beatsOkResponse()))())
        return Promise.resolve(beatsOkResponse()) // /beats/window
      })
    }

    it('uses the persisted channel and skips detection when prior state is found', async () => {
      const fetchMock = mockFetchWithFileState(
        fileStateResponse({
          found: true,
          channel: 'channel 2',
          beats: [],
          bad_data_marks: [{ id: 1, start: 2, stop: 5 }],
        }),
      )
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => {
        const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/files/state'))
        expect(call).toBeTruthy()
        const url = new URL(String(call![0]))
        expect(url.searchParams.get('path')).toBe('/data/57.txt')
      })

      // Deliberately different from defaultChannel — proves the persisted
      // choice wins.
      await waitFor(() => {
        const select = screen.getByTestId('channel-select') as HTMLSelectElement
        expect(select.value).toBe('channel 2')
      })
      await waitFor(() => {
        expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
      })

      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/beats/detect'))).toBe(false)
    })

    it('passes the persisted bad-data marks through to EcgGraph as initialBadDataMarks', async () => {
      const fetchMock = mockFetchWithFileState(
        fileStateResponse({
          found: true,
          channel: 'channel 2',
          beats: [],
          bad_data_marks: [{ id: 1, start: 2, stop: 5 }],
        }),
      )
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => {
        expect(screen.getAllByTestId('bad-data-mark')).toHaveLength(1)
      })
    })

    // Regression coverage for the Critical finding this task fixes: before
    // the `hydrationChecked` gate, `{#key activeChannel}` only remounts
    // EcgGraph when the KEY VALUE actually changes. If the persisted channel
    // happens to equal `defaultChannel` — reopening a file on the same
    // channel you left it on, the most common reopen case — `activeChannel`
    // is set to the same value it already held, so no remount ever fires,
    // and the EcgGraph instance already constructed at t=0 (seeded with
    // `initialBadDataMarks=[]`, before hydration resolved) never picks up
    // the hydrated marks. The test above already covers the
    // different-channel case (which happened to work before this fix only
    // because the channel mismatch forced a remount); this one isolates the
    // same-channel case specifically.
    it('preserves persisted bad-data marks on reopen even when the persisted channel equals defaultChannel', async () => {
      const fetchMock = mockFetchWithFileState(
        fileStateResponse({
          found: true,
          channel: 'channel 1', // same as defaultChannel below
          beats: [],
          bad_data_marks: [{ id: 1, start: 2, stop: 5 }],
        }),
      )
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => {
        expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
      })
      await waitFor(() => {
        expect(screen.getAllByTestId('bad-data-mark')).toHaveLength(1)
      })
    })

    // Regression coverage for the Important finding that EcgGraph's mount
    // is a real, wasted (and possibly visibly-wrong) fetch against
    // `defaultChannel`, not an inert one: EcgGraph's own onMount fires
    // synchronously at initial render, before `getFileState`'s network round
    // trip can possibly have resolved. This proves the fix — gating
    // EcgGraph's very existence on `hydrationChecked` — by holding
    // `getFileState`'s promise pending and asserting zero graph-related
    // fetches have fired, then resolving it and asserting they now occur,
    // for the correct (hydrated) channel.
    it('does not mount EcgGraph (no /channels/window or /beats/window fetch) until GET /files/state resolves', async () => {
      let resolveFileState: ((value: unknown) => void) | undefined
      const fileStatePromise = new Promise((resolve) => {
        resolveFileState = resolve
      })

      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return fileStatePromise
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        return Promise.resolve(beatsOkResponse()) // /beats/window
      })
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      // Only the hydration check itself should have fired so far.
      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(1))
      expect(String(fetchMock.mock.calls[0][0])).toContain('/files/state')
      expect(screen.queryByTestId('ecg-graph')).not.toBeInTheDocument()

      // Give any (incorrect) premature EcgGraph mount a chance to fire its
      // fetches before resolving hydration, to prove the gate actually held.
      await Promise.resolve()
      await Promise.resolve()
      expect(fetchMock.mock.calls.length).toBe(1)

      resolveFileState!(fileStateResponse({ found: true, channel: 'channel 2', bad_data_marks: [] }))

      await waitFor(() => {
        expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
      })
      await waitFor(() => {
        const calledUrls = fetchMock.mock.calls.map((call) => String(call[0]))
        expect(calledUrls.some((url) => url.includes('/channels/window'))).toBe(true)
        expect(calledUrls.some((url) => url.includes('/beats/window'))).toBe(true)
      })
    })

    // Regression coverage for the Important finding that nothing coordinated
    // the hydration write with a technician manually changing channels while
    // `getFileState` was still in flight. Rather than adding a request-id
    // guard to hydration itself, the fix makes the `<select>` structurally
    // unable to fire `handleChannelChange` until hydration has settled —
    // this proves that gate directly via the `disabled` attribute's
    // lifecycle across the hydration promise.
    it('disables the channel select while hydration is in flight and re-enables it once GET /files/state settles', async () => {
      let resolveFileState: ((value: unknown) => void) | undefined
      const fileStatePromise = new Promise((resolve) => {
        resolveFileState = resolve
      })

      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return fileStatePromise
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        return Promise.resolve(beatsOkResponse()) // /beats/window
      })
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      const select = screen.getByTestId('channel-select') as HTMLSelectElement
      expect(select).toBeDisabled()

      resolveFileState!(fileStateResponse())

      await waitFor(() => {
        expect(select).not.toBeDisabled()
      })
    })

    // Part 3 of this wave's fix: extends the same hydration-settling
    // protection to `ArrhythmiaControls` — `activeChannel` still holds
    // `defaultChannel` until `getFileState` resolves, so a rerun started
    // before then would run against the wrong (pre-hydration) channel on
    // reopen. Mirrors the channel `<select>`'s own equivalent test above,
    // but for `ArrhythmiaControls`' three method buttons via its
    // `disabled` prop (ANDed with its own `runState`-based disabling).
    it('disables the ArrhythmiaControls buttons while hydration is in flight and re-enables them once GET /files/state settles', async () => {
      let resolveFileState: ((value: unknown) => void) | undefined
      const fileStatePromise = new Promise((resolve) => {
        resolveFileState = resolve
      })

      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return fileStatePromise
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        return Promise.resolve(beatsOkResponse()) // /beats/window
      })
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      expect(screen.getByTestId('run-heuristic-button')).toBeDisabled()
      expect(screen.getByTestId('run-unsupervised-button')).toBeDisabled()
      expect(screen.getByTestId('run-both-button')).toBeDisabled()

      resolveFileState!(fileStateResponse())

      await waitFor(() => {
        expect(screen.getByTestId('run-heuristic-button')).not.toBeDisabled()
      })
      expect(screen.getByTestId('run-unsupervised-button')).not.toBeDisabled()
      expect(screen.getByTestId('run-both-button')).not.toBeDisabled()
    })

    it('falls back to defaultChannel with no prior state (found: false)', async () => {
      const fetchMock = mockFetchWithFileState(fileStateResponse({ found: false }))
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => {
        expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/files/state'))).toBe(true)
      })

      const select = screen.getByTestId('channel-select') as HTMLSelectElement
      expect(select.value).toBe('channel 1')

      await waitFor(() => {
        expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
      })
      expect(screen.queryByTestId('bad-data-mark')).not.toBeInTheDocument()
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/beats/detect'))).toBe(false)
    })

    it('falls back to defaultChannel exactly as the not-found case when GET /files/state fails', async () => {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return Promise.reject(new Error('network down'))
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        return Promise.resolve(beatsOkResponse()) // /beats/window
      })
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      const select = screen.getByTestId('channel-select') as HTMLSelectElement
      expect(select.value).toBe('channel 1')

      await waitFor(() => {
        expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 1')
      })
      expect(screen.queryByTestId('bad-data-mark')).not.toBeInTheDocument()
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/beats/detect'))).toBe(false)
    })
  })

  // Task 2 of F5: remembers the technician's channel choice per file via
  // `PUT /files/channel`, fired (fire-and-forget) right after a successful
  // channel switch — see `handleChannelChange`'s `putChannel` call.
  describe('channel choice persistence: PUT /files/channel on channel switch', () => {
    it('persists the newly selected channel alongside the existing detectBeats call', async () => {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return Promise.resolve(fileStateResponse())
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/files/channel')) {
          return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', channel: 'channel 2', error: null }) })
        }
        return Promise.resolve(beatsOkResponse()) // /beats/detect, /beats/window
      })
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

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
        expect(fetchMock).toHaveBeenCalledWith(
          'http://127.0.0.1:8000/files/channel',
          expect.objectContaining({
            method: 'PUT',
            body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 2' }),
          }),
        )
      })
    })

    it('does not block the channel switch when putChannel fails', async () => {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return Promise.resolve(fileStateResponse())
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/files/channel')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'error', channel: null, error: 'boom' }),
          })
        }
        return Promise.resolve(beatsOkResponse()) // /beats/detect, /beats/window
      })
      vi.stubGlobal('fetch', fetchMock)

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

      const select = screen.getByTestId('channel-select') as HTMLSelectElement
      await fireEvent.change(select, { target: { value: 'channel 2' } })

      await waitFor(() => {
        expect(screen.getByTestId('ecg-graph')).toHaveAttribute('data-channel', 'channel 2')
      })
    })
  })

  // Closes F4's Important finding #2 (deferred to F5, see the plan): a
  // successful confirm/reject/reassign must refresh EcgGraph's beat markers
  // immediately, via the same `beatsRefreshToken` mechanism
  // ArrhythmiaControls' `onComplete` already uses — proven here by the same
  // observable signal the arrhythmia-refresh tests above use: an additional
  // `/beats/window` fetch firing after the action succeeds.
  describe('category update refreshes graph beat markers', () => {
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

    function clickBeatMarker() {
      const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
      const plotWidthPx = parseFloat(over.style.width)
      const clickX = plotWidthPx * ((1 - 0) / (2 - 0))
      over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: clickX, clientY: 100, bubbles: true }))
      document.dispatchEvent(new MouseEvent('mouseup', { clientX: clickX, clientY: 100, bubbles: true }))
    }

    it('refreshes the graph beats after a successful confirm action', async () => {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return Promise.resolve(fileStateResponse())
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/window')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [targetBeat], count: 1, error: null }),
          })
        }
        if (url.includes('/files/beats/category')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', ts: 1, review_state: 'confirmed', reassigned_category: null, error: null }),
          })
        }
        if (url.includes('/files/beats')) {
          return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', count: 1, error: null }) })
        }
        return Promise.resolve(beatsOkResponse())
      })
      vi.stubGlobal('fetch', fetchMock)

      // EcgGraph mounts only after ReviewWorkspace's mount-time hydration
      // settles, after `render()` returns — see the identical rationale in
      // "shows the selected beat after a click on its marker..." above.
      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

        clickBeatMarker()

        await waitFor(() => {
          expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
        })

        const beatsWindowCallsBefore = fetchMock.mock.calls.filter((call) =>
          String(call[0]).includes('/beats/window'),
        ).length

        await fireEvent.click(screen.getByTestId('confirm-button'))

        await waitFor(() => {
          expect(screen.getByTestId('review-state')).toHaveTextContent('confirmed')
        })

        await waitFor(() => {
          const beatsWindowCallsAfter = fetchMock.mock.calls.filter((call) =>
            String(call[0]).includes('/beats/window'),
          ).length
          expect(beatsWindowCallsAfter).toBe(beatsWindowCallsBefore + 1)
        })
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
    })

    // Part 2 of this wave's fix: `handleCategoryUpdated` must stay on the
    // pre-existing `refreshGraphBeats`-only path — `PATCH
    // /files/beats/category` already writes straight to SQLite via
    // `db.update_beat_category`, so a category action must NOT also trigger
    // a wholesale `POST /files/beats` re-persist (that's reserved for
    // `handleArrhythmiaComplete`, proven separately in the "arrhythmia
    // re-run wiring" describe block above). Exactly one `/files/beats` call
    // total — from the initial `ensureChannelPersisted` on beat selection —
    // proves `handleCategoryUpdated`'s path stays undisturbed.
    it('does not trigger an extra POST /files/beats when a category action succeeds', async () => {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return Promise.resolve(fileStateResponse())
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/window')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [targetBeat], count: 1, error: null }),
          })
        }
        if (url.includes('/files/beats/category')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', ts: 1, review_state: 'confirmed', reassigned_category: null, error: null }),
          })
        }
        if (url.includes('/files/beats')) {
          return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', count: 1, error: null }) })
        }
        return Promise.resolve(beatsOkResponse())
      })
      vi.stubGlobal('fetch', fetchMock)

      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

        clickBeatMarker()

        await waitFor(() => {
          expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
        })

        const persistCallsAfterSelection = fetchMock.mock.calls.filter(
          (call) => new URL(String(call[0])).pathname === '/files/beats',
        ).length
        expect(persistCallsAfterSelection).toBe(1)

        await fireEvent.click(screen.getByTestId('confirm-button'))

        await waitFor(() => {
          expect(screen.getByTestId('review-state')).toHaveTextContent('confirmed')
        })

        const persistCallsAfterConfirm = fetchMock.mock.calls.filter(
          (call) => new URL(String(call[0])).pathname === '/files/beats',
        ).length
        expect(persistCallsAfterConfirm).toBe(1)
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
    })

    it('does not refresh the graph when a category update fails', async () => {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/state')) return Promise.resolve(fileStateResponse())
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/beats/window')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'ok', beats: [targetBeat], count: 1, error: null }),
          })
        }
        if (url.includes('/files/beats/category')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: 'error', ts: null, review_state: null, reassigned_category: null, error: 'boom' }),
          })
        }
        if (url.includes('/files/beats')) {
          return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', count: 1, error: null }) })
        }
        return Promise.resolve(beatsOkResponse())
      })
      vi.stubGlobal('fetch', fetchMock)

      // Same rationale as the sibling test above: hold the override for the
      // whole body since EcgGraph mounts asynchronously, after `render()`
      // returns.
      const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
      try {
        render(ReviewWorkspace, {
          props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
        })

        await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

        clickBeatMarker()

        await waitFor(() => {
          expect(screen.getByTestId('beat-category-panel')).toBeInTheDocument()
        })

        const beatsWindowCallsBefore = fetchMock.mock.calls.filter((call) =>
          String(call[0]).includes('/beats/window'),
        ).length

        await fireEvent.click(screen.getByTestId('confirm-button'))

        await waitFor(() => {
          expect(screen.getByTestId('validation-error')).toHaveTextContent('boom')
        })

        const beatsWindowCallsAfter = fetchMock.mock.calls.filter((call) =>
          String(call[0]).includes('/beats/window'),
        ).length
        expect(beatsWindowCallsAfter).toBe(beatsWindowCallsBefore)
      } finally {
        if (originalClientWidth) {
          Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
        } else {
          delete (HTMLElement.prototype as any).clientWidth
        }
      }
    })
  })

  // Task 4 of F5: report export. The technician picks an output directory
  // via the native Electron dialog (`window.api.pickOutputDirectory`, F1),
  // then `POST /files/report` (`generateReport` in `lib/api/persistence.ts`)
  // writes the report there. Canceling the native dialog resolves `null` and
  // must be a no-op — never call the API with a missing directory.
  describe('report export: Generate Report button', () => {
    function mockFetchForReport(reportResponse?: () => { ok: boolean; json: () => Promise<unknown> }) {
      return vi.fn().mockImplementation((url: string) => {
        if (url.includes('/files/report') && reportResponse) return Promise.resolve(reportResponse())
        if (url.includes('/channels/window')) return Promise.resolve(channelWindowOkResponse())
        if (url.includes('/files/state')) return Promise.resolve(fileStateResponse())
        return Promise.resolve(beatsOkResponse()) // /beats/window
      })
    }

    it('opens the native directory picker, then calls generateReport with the chosen path', async () => {
      const fetchMock = mockFetchForReport(() => ({
        ok: true,
        json: async () => ({ status: 'ok', output_path: '/Users/tech/reports/57.xlsx', error: null }),
      }))
      vi.stubGlobal('fetch', fetchMock)
      ;(window as any).api = {
        pickOutputDirectory: vi.fn().mockResolvedValue('/Users/tech/reports'),
      }

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

      await fireEvent.click(screen.getByTestId('generate-report-button'))

      expect((window as any).api.pickOutputDirectory).toHaveBeenCalled()
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'http://127.0.0.1:8000/files/report',
          expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ path: '/data/57.txt', output_dir: '/Users/tech/reports' }),
          }),
        )
      })
    })

    it('does nothing when the directory picker is canceled', async () => {
      const fetchMock = mockFetchForReport()
      vi.stubGlobal('fetch', fetchMock)
      ;(window as any).api = {
        pickOutputDirectory: vi.fn().mockResolvedValue(null),
      }

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

      await fireEvent.click(screen.getByTestId('generate-report-button'))

      expect((window as any).api.pickOutputDirectory).toHaveBeenCalled()
      // Give any (incorrect) fire-and-forget call a chance to fire before asserting.
      await Promise.resolve()
      await Promise.resolve()
      expect(fetchMock.mock.calls.some((call) => String(call[0]).includes('/files/report'))).toBe(false)
    })

    it('shows the real output path on success', async () => {
      const fetchMock = mockFetchForReport(() => ({
        ok: true,
        json: async () => ({ status: 'ok', output_path: '/Users/tech/reports/57.xlsx', error: null }),
      }))
      vi.stubGlobal('fetch', fetchMock)
      ;(window as any).api = {
        pickOutputDirectory: vi.fn().mockResolvedValue('/Users/tech/reports'),
      }

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

      await fireEvent.click(screen.getByTestId('generate-report-button'))

      await waitFor(() => {
        expect(screen.getByTestId('report-success')).toHaveTextContent('/Users/tech/reports/57.xlsx')
      })
    })

    it('shows a clean inline error when report generation fails, without crashing', async () => {
      const fetchMock = mockFetchForReport(() => ({
        ok: true,
        json: async () => ({
          status: 'error',
          output_path: null,
          error: 'No persisted data for this file — run POST /files/beats first',
        }),
      }))
      vi.stubGlobal('fetch', fetchMock)
      ;(window as any).api = {
        pickOutputDirectory: vi.fn().mockResolvedValue('/Users/tech/reports'),
      }

      render(ReviewWorkspace, {
        props: { path: '/data/57.txt', channels: ['channel 1', 'channel 2'], defaultChannel: 'channel 1' },
      })

      await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3))

      await fireEvent.click(screen.getByTestId('generate-report-button'))

      await waitFor(() => {
        expect(screen.getByTestId('report-error')).toHaveTextContent(
          'No persisted data for this file — run POST /files/beats first',
        )
      })
      expect(screen.getByTestId('review-workspace')).toBeInTheDocument()
    })
  })
})
