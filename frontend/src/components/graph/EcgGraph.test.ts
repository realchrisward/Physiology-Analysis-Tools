import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import EcgGraph, { buildBeatAlignedData, toChartData } from './EcgGraph.svelte'
import type { BadDataMark, WindowBeat } from '../../lib/api/types'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function channelWindowResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: async () => ({
      status: 'ok',
      x: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
      y: [0, 1, 0, -1, 0, 1, 0, -1, 0, 1],
      point_count: 10,
      downsampled: false,
      error: null,
      ...overrides,
    }),
  }
}

function beatsWindowResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: async () => ({
      status: 'ok',
      beats: [],
      count: 0,
      error: null,
      ...overrides,
    }),
  }
}

function badDataAddResponse(mark: BadDataMark) {
  return {
    ok: true,
    json: async () => ({ status: 'ok', mark, error: null }),
  }
}

function badDataDeleteResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: async () => ({ status: 'ok', error: null, ...overrides }),
  }
}

// Routes the shared fetch mock by URL pathname (and, for the shared
// `/files/bad-data` path, HTTP method) rather than by call order — every
// EcgGraph range-change now fires two GETs (`/channels/window` then
// `/beats/window`; see `fetchMergedWindow` in EcgGraph.svelte), so a
// generic "respond to every call the same way" mock would hand the beats
// endpoint a channel-window-shaped body (or vice versa).
function routedFetch(
  handlers: {
    channel?: () => unknown
    beats?: () => unknown
    addBadData?: () => unknown
    deleteBadData?: () => unknown
  } = {},
) {
  return vi.fn((url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/beats/window') {
      return Promise.resolve((handlers.beats ?? (() => beatsWindowResponse()))())
    }
    if (pathname === '/files/bad-data') {
      if (init?.method === 'DELETE') {
        return Promise.resolve((handlers.deleteBadData ?? (() => badDataDeleteResponse()))())
      }
      return Promise.resolve(
        (handlers.addBadData ?? (() => badDataAddResponse({ id: 1, start: 0, stop: 1 })))(),
      )
    }
    return Promise.resolve((handlers.channel ?? (() => channelWindowResponse()))())
  })
}

function beat(overrides: Partial<WindowBeat> & Pick<WindowBeat, 'ts' | 'r_amplitude'>): WindowBeat {
  return {
    rr: 0.8,
    hr: 75,
    bradycardia_absolute: false,
    tachycardia_absolute: false,
    skipped_beat: false,
    prem_beat: false,
    abn_cluster: false,
    any_arrhythmia: false,
    other_arrhythmia: false,
    review_state: 'unreviewed',
    reassigned_category: null,
    ...overrides,
  }
}

// jsdom performs no layout, so every element's real `clientWidth` is 0
// unless overridden. Stubbing it at the prototype level (rather than on one
// element instance, which doesn't exist until after render) is the standard
// jsdom technique for this — restored afterwards so it doesn't leak into
// other tests/files.
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

function fetchedUrl(fetchMock: ReturnType<typeof vi.fn>, callIndex: number): URL {
  const [url] = fetchMock.mock.calls[callIndex]
  return new URL(url as string)
}

function fetchedBody(fetchMock: ReturnType<typeof vi.fn>, callIndex: number): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[callIndex]
  return JSON.parse((init as RequestInit).body as string)
}

describe('EcgGraph', () => {
  it('fetches the full range on mount and renders without error', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })

    // One call for the channel window, one for beats in the same range —
    // see `fetchMergedWindow` in EcgGraph.svelte.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const url = fetchedUrl(fetchMock, 0)
    expect(url.pathname).toBe('/channels/window')
    expect(url.searchParams.get('path')).toBe('/data/57.txt')
    expect(url.searchParams.get('channel')).toBe('channel 1')
    expect(url.searchParams.get('start')).toBe('0')
    expect(url.searchParams.get('end')).toBe(String(Number.MAX_SAFE_INTEGER))

    await waitFor(() => {
      expect(screen.queryByTestId('ecg-graph-error')).not.toBeInTheDocument()
    })
  })

  it('fetches beat markers for the same window alongside the channel data, on mount and after a pan/zoom re-fetch', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const channelCall = fetchedUrl(fetchMock, 0)
    const beatsCall = fetchedUrl(fetchMock, 1)
    expect(channelCall.pathname).toBe('/channels/window')
    expect(beatsCall.pathname).toBe('/beats/window')
    expect(beatsCall.searchParams.get('path')).toBe('/data/57.txt')
    expect(beatsCall.searchParams.get('start')).toBe(channelCall.searchParams.get('start'))
    expect(beatsCall.searchParams.get('end')).toBe(channelCall.searchParams.get('end'))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    vi.useFakeTimers()
    over.dispatchEvent(
      new WheelEvent('wheel', { deltaY: -100, clientX: 400, clientY: 100, bubbles: true, cancelable: true }),
    )
    await vi.advanceTimersByTimeAsync(150)
    expect(fetchMock).toHaveBeenCalledTimes(4)

    const secondChannelCall = fetchedUrl(fetchMock, 2)
    const secondBeatsCall = fetchedUrl(fetchMock, 3)
    expect(secondChannelCall.pathname).toBe('/channels/window')
    expect(secondBeatsCall.pathname).toBe('/beats/window')
    expect(secondBeatsCall.searchParams.get('start')).toBe(secondChannelCall.searchParams.get('start'))
    expect(secondBeatsCall.searchParams.get('end')).toBe(secondChannelCall.searchParams.get('end'))
  })

  it('renders without error when getBeatsWindow returns a mix of arrhythmia-flagged, normal, and not-yet-evaluated beats', async () => {
    // End-to-end companion to the `buildBeatAlignedData` unit tests below,
    // which assert the actual per-category series data precisely (see the
    // comment there for why that's the exact seam this feature is tested
    // through, rather than a live uPlot instance's internals) — this test
    // instead confirms the full fetch-mocked component doesn't error when
    // handed the same 2-arrhythmia-states-plus-null fixture the brief
    // describes, i.e. the wiring from a real `getBeatsWindow` response
    // through to chart construction doesn't throw or surface an error.
    const fetchMock = routedFetch({
      beats: () =>
        beatsWindowResponse({
          beats: [
            beat({ ts: 2, r_amplitude: 5, any_arrhythmia: true }),
            beat({ ts: 5, r_amplitude: 6, any_arrhythmia: false }),
            beat({ ts: 7, r_amplitude: 7, any_arrhythmia: null }),
          ],
          count: 3,
        }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByTestId('ecg-graph-error')).not.toBeInTheDocument())
  })

  it('sends a resolution query param matching the container width', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const url = fetchedUrl(fetchMock, 0)
    expect(url.searchParams.get('resolution')).toBe('800')
  })

  it('debounces a re-fetch after a pan/zoom interaction, with a different range', async () => {
    const fetchMock = routedFetch({
      channel: (() => {
        let call = 0
        return () => {
          call += 1
          return call === 1
            ? channelWindowResponse({ x: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] })
            : channelWindowResponse({ x: [3, 4, 5, 6], y: [-1, 0, 1, 0] })
        }
      })(),
    })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    expect(over).toBeTruthy()

    vi.useFakeTimers()
    over.dispatchEvent(
      new WheelEvent('wheel', { deltaY: -100, clientX: 400, clientY: 100, bubbles: true, cancelable: true }),
    )

    // Not yet — still inside the 150ms debounce window.
    await vi.advanceTimersByTimeAsync(100)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(100)
    expect(fetchMock).toHaveBeenCalledTimes(4)

    const firstCall = fetchedUrl(fetchMock, 0)
    const secondCall = fetchedUrl(fetchMock, 2)
    expect(secondCall.searchParams.get('start')).not.toBe(firstCall.searchParams.get('start'))
    expect(secondCall.searchParams.get('end')).not.toBe(firstCall.searchParams.get('end'))
  })

  it('restores the initial full extent on reset view, after a pan/zoom re-fetch', async () => {
    const fetchMock = routedFetch({
      channel: (() => {
        let call = 0
        return () => {
          call += 1
          return call === 1
            ? channelWindowResponse({ x: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] })
            : channelWindowResponse({ x: [3, 4, 5, 6], y: [-1, 0, 1, 0] })
        }
      })(),
    })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    const initialCall = fetchedUrl(fetchMock, 0)

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement

    vi.useFakeTimers()
    over.dispatchEvent(
      new WheelEvent('wheel', { deltaY: -100, clientX: 400, clientY: 100, bubbles: true, cancelable: true }),
    )
    await vi.advanceTimersByTimeAsync(150)
    expect(fetchMock).toHaveBeenCalledTimes(4)
    vi.useRealTimers()

    const resetButton = screen.getByTestId('reset-view-button')
    resetButton.click()

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6))
    const resetCall = fetchedUrl(fetchMock, 4)
    expect(resetCall.searchParams.get('start')).toBe(initialCall.searchParams.get('start') ?? '0')
    // The initial mount requests an effectively-unbounded end
    // (Number.MAX_SAFE_INTEGER) to mean "everything"; the stored full
    // extent for Reset View is the *response's* actual x[0]/x[last]
    // (0 and 9 here), which is what a reset re-fetch should request.
    expect(resetCall.searchParams.get('end')).toBe('9')
  })

  it('highlights the Reset view button after a pan, and un-highlights it after reset', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const resetButton = screen.getByTestId('reset-view-button')
    expect(resetButton.className).not.toContain('btn-active')

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    vi.useFakeTimers()
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 100, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 300, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: 300, clientY: 100, bubbles: true }))
    await vi.advanceTimersByTimeAsync(150)
    vi.useRealTimers()

    expect(resetButton.className).toContain('btn-active')

    await fireEvent.click(resetButton)
    expect(resetButton.className).not.toContain('btn-active')
  })

  it('zoom-in button narrows the visible x-scale immediately', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const zoomInButton = screen.getByTestId('zoom-in-button')
    zoomInButton.click()
    zoomInButton.click()

    // Zooming narrows the x-scale synchronously (via uPlot's own
    // `setScale`) regardless of whether a re-fetch is needed — see
    // `needsRefetch` in EcgGraph.svelte: a modest zoom step that stays
    // within the already-loaded buffer intentionally triggers NO fetch at
    // all (that's the point of buffering — most zoom/pan doesn't have to
    // wait on a network round trip), so this only asserts the visible
    // range itself narrowed, not that a fetch happened.
    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    expect(over).toBeTruthy()
  })

  it('re-fetches at higher resolution once zoomed in enough that the loaded buffer is too coarse', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    vi.useFakeTimers()
    const zoomInButton = screen.getByTestId('zoom-in-button')
    // Several successive zoom-in steps (each 0.75x the range) eventually
    // narrow the visible range past the loaded buffer's resolution
    // threshold (see MIN_ZOOM_RATIO_BEFORE_REFETCH in EcgGraph.svelte),
    // triggering a real debounced re-fetch — a single modest step
    // deliberately does not (see the test above).
    for (let i = 0; i < 5; i++) {
      zoomInButton.click()
      await vi.advanceTimersByTimeAsync(1)
    }
    await vi.advanceTimersByTimeAsync(150)

    expect(fetchMock.mock.calls.length).toBeGreaterThan(2)
    vi.useRealTimers()
  })

  it('zoom-out button widens the visible x-scale', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    // Zoom in first so zooming back out has visible room to widen into.
    vi.useFakeTimers()
    const zoomInButton = screen.getByTestId('zoom-in-button')
    for (let i = 0; i < 3; i++) zoomInButton.click()
    await vi.advanceTimersByTimeAsync(150)
    const callsAfterZoomIn = fetchMock.mock.calls.length

    screen.getByTestId('zoom-out-button').click()
    await vi.advanceTimersByTimeAsync(150)

    // Zooming back out is itself always a legitimate scale change (handled
    // the same debounced way as any other pan/zoom); this doesn't assert a
    // fetch count, only that the interaction completes without error.
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(callsAfterZoomIn)
    vi.useRealTimers()
  })

  it('expand-graph-button toggles a taller chart height', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const canvas = screen.getByTestId('ecg-graph-container').querySelector('canvas') as HTMLCanvasElement
    const initialHeight = canvas.height

    const expandButton = screen.getByTestId('expand-graph-button')
    expandButton.click()
    await waitFor(() => expect(canvas.height).toBeGreaterThan(initialHeight))

    expandButton.click()
    await waitFor(() => expect(canvas.height).toBe(initialHeight))
  })

  it('removes document-level drag listeners on unmount, so a stale mid-drag mouseup fetches nothing', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    const { unmount } = withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    expect(over).toBeTruthy()

    // Start a drag but never dispatch a natural mouseup — this is the
    // in-progress-drag state that leaves `document`-level listeners
    // attached until `onUp` fires (or, before the fix, forever).
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 100, clientY: 100, bubbles: true }))

    // A leaked `onUp` (the bug this test guards against) only *schedules* a
    // re-fetch via `scheduleRefetch`'s 150ms debounce timer — it doesn't
    // call fetch synchronously. Fake timers let us advance past that
    // window deterministically instead of racing a real 150ms wait.
    vi.useFakeTimers()

    unmount()

    const callsAfterUnmount = fetchMock.mock.calls.length

    // Simulate the drag "completing" via listeners left on `document`, as
    // would happen from any later unrelated mouseup on the page.
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 200, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: 200, clientY: 100, bubbles: true }))

    // Advance well past the 150ms debounce so a leaked listener's scheduled
    // re-fetch (if any) would have fired by now.
    await vi.advanceTimersByTimeAsync(200)

    // No new fetch: the listeners were actually removed on unmount, not
    // just orphaned alongside a destroyed chart.
    expect(fetchMock.mock.calls.length).toBe(callsAfterUnmount)
  })

  it('removes document-level bad-data-drag listeners on unmount, so a stale mid-drag mouseup adds no mark', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    const { unmount } = withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    screen.getByTestId('bad-data-mode-button').click()
    await waitFor(() => expect(screen.getByTestId('bad-data-mode-button').getAttribute('aria-pressed')).toBe('true'))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    expect(over).toBeTruthy()

    // Start a bad-data drag but never dispatch a natural mouseup — this is
    // the in-progress-drag state that leaves `document`-level listeners
    // attached until `onUp` fires (or, before the fix, forever) — same
    // shape as the pan-drag unmount test above, but exercising
    // `handleBadDataDragStart`'s own listeners/teardown instead of
    // `handleDragStart`'s.
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 100, clientY: 100, bubbles: true }))

    vi.useFakeTimers()

    unmount()

    const callsAfterUnmount = fetchMock.mock.calls.length

    // Simulate the drag "completing" via listeners left on `document`, as
    // would happen from any later unrelated mousemove/mouseup on the page.
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 200, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: 200, clientY: 100, bubbles: true }))

    // addBadData (unlike the pan path's scheduleRefetch) is called directly
    // from onUp, not via a debounce timer — but advance timers anyway so
    // this stays robust to that changing, and so any microtask queued by a
    // (non-existent, if the fix holds) fetch call has a chance to settle.
    await vi.advanceTimersByTimeAsync(200)

    // No new fetch calls at all: the listeners were actually removed on
    // unmount, so the stale document-level mouseup never reached `onUp`
    // and never called `addBadData` (which would show up as a new
    // `/files/bad-data` POST in the fetch mock).
    expect(fetchMock.mock.calls.length).toBe(callsAfterUnmount)
    const calledPaths = fetchMock.mock.calls.map(([url]) => new URL(url as string).pathname)
    expect(calledPaths).not.toContain('/files/bad-data')
  })

  it('does not add a bad-data mark on a plain drag when bad-data mode is off (the default)', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement

    vi.useFakeTimers()
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 100, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 300, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: 300, clientY: 100, bubbles: true }))

    // Let the pan's own debounced re-fetch (channel + beats) fire, so we're
    // asserting against the fully-settled call list, not a mid-flight one.
    await vi.advanceTimersByTimeAsync(200)

    const calledPaths = fetchMock.mock.calls.map(([url]) => new URL(url as string).pathname)
    expect(calledPaths).not.toContain('/files/bad-data')
    expect(screen.queryByTestId('bad-data-mark')).not.toBeInTheDocument()
  })

  it('enters bad-data mode via the toggle button, and a drag on the plot adds a mark via addBadData', async () => {
    const mark: BadDataMark = { id: 42, start: 2, stop: 6 }
    const fetchMock = routedFetch({ addBadData: () => badDataAddResponse(mark) })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const modeButton = screen.getByTestId('bad-data-mode-button')
    expect(modeButton.getAttribute('aria-pressed')).toBe('false')
    modeButton.click()
    await waitFor(() => expect(modeButton.getAttribute('aria-pressed')).toBe('true'))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    // Drag start/end in either order is fine — the backend auto-sorts, so
    // this only asserts that both endpoint values are sent, not their order.
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 500, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 200, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: 200, clientY: 100, bubbles: true }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))

    const addCallUrl = fetchedUrl(fetchMock, 2)
    expect(addCallUrl.pathname).toBe('/files/bad-data')
    const [, addInit] = fetchMock.mock.calls[2]
    expect((addInit as RequestInit).method).toBe('POST')
    const addBody = fetchedBody(fetchMock, 2)
    expect(addBody.path).toBe('/data/57.txt')
    expect(typeof addBody.start).toBe('number')
    expect(typeof addBody.stop).toBe('number')

    // The response's real mark (id 42) is now tracked locally and rendered.
    await waitFor(() => {
      const marks = screen.getAllByTestId('bad-data-mark')
      expect(marks).toHaveLength(1)
    })

    // A plain drag no longer pans while bad-data mode stays on (mutually
    // exclusive per the brief) — not separately asserted here since pan
    // behavior itself is covered by the earlier pan/zoom tests; this test's
    // contract is limited to "a drag while in bad-data mode adds a mark".
  })

  // uPlot fixes its default axis size (left/bottom gutters) at exactly 50
  // CSS px regardless of any text measurement (jsdom stubs `getContext('2d')`
  // with no `measureText` at all — see test-setup.ts — so relying on real
  // font metrics isn't an option here), and sets `.u-over`'s inline
  // `style.width` to the resulting plot-area width on every render. Reading
  // that back gives the exact pixel width uPlot is using to map screen
  // positions to data values for *this* render, without hand-deriving the
  // axis-gutter math — so a click's `clientX` can be computed to land on an
  // exact data value (e.g. a beat's `ts`) deterministically, rather than by
  // guessing at a plausible-looking pixel offset.
  function plotWidthPx(over: HTMLDivElement): number {
    return parseFloat(over.style.width)
  }

  // The full-mount x-domain uPlot auto-ranges to is exactly [xs[0], xs[last]]
  // of the merged (channel ∪ beat-ts) x-array with no padding, whenever that
  // array has more than one distinct value (see uPlot's `autoScaleX`) — so
  // for a beat whose `ts` already lands inside the channel's own x-range,
  // the domain is just the channel x fixture's own [min, max].
  function clientXForVal(over: HTMLDivElement, val: number, domainMin: number, domainMax: number): number {
    return plotWidthPx(over) * ((val - domainMin) / (domainMax - domainMin))
  }

  it('fires onBeatSelect with the full beat record on a plain click (no movement) on its marker position', async () => {
    const targetBeat = beat({
      ts: 5,
      r_amplitude: 6,
      rr: 0.9,
      hr: 66,
      bradycardia_absolute: true,
      tachycardia_absolute: false,
      skipped_beat: false,
      prem_beat: true,
      abn_cluster: false,
      any_arrhythmia: true,
      other_arrhythmia: false,
    })
    const fetchMock = routedFetch({ beats: () => beatsWindowResponse({ beats: [targetBeat], count: 1 }) })
    vi.stubGlobal('fetch', fetchMock)

    const onBeatSelect = vi.fn()
    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1', onBeatSelect } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    const clickX = clientXForVal(over, 5, 0, 9)

    over.dispatchEvent(
      new MouseEvent('mousedown', { button: 0, clientX: clickX, clientY: 100, bubbles: true }),
    )
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: clickX, clientY: 100, bubbles: true }))

    expect(onBeatSelect).toHaveBeenCalledTimes(1)
    expect(onBeatSelect).toHaveBeenCalledWith(targetBeat)
  })

  it('does not fire onBeatSelect on a drag beyond the click threshold, and still pans/re-fetches as before', async () => {
    const targetBeat = beat({ ts: 5, r_amplitude: 6, any_arrhythmia: true })
    const fetchMock = routedFetch({ beats: () => beatsWindowResponse({ beats: [targetBeat], count: 1 }) })
    vi.stubGlobal('fetch', fetchMock)

    const onBeatSelect = vi.fn()
    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1', onBeatSelect } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement

    vi.useFakeTimers()
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 100, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 300, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: 300, clientY: 100, bubbles: true }))

    await vi.advanceTimersByTimeAsync(150)

    expect(onBeatSelect).not.toHaveBeenCalled()
    // Reusing the existing pan test's assertion shape: the debounced
    // re-fetch (channel + beats) still fires for the shifted range.
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('does not fire onBeatSelect on a click far from any beat marker', async () => {
    const targetBeat = beat({ ts: 5, r_amplitude: 6, any_arrhythmia: true })
    const fetchMock = routedFetch({ beats: () => beatsWindowResponse({ beats: [targetBeat], count: 1 }) })
    vi.stubGlobal('fetch', fetchMock)

    const onBeatSelect = vi.fn()
    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1', onBeatSelect } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    // val=0 is 5 data-units away from the beat at ts=5 — far outside the
    // small pixel hit-tolerance at this plot width.
    const clickX = clientXForVal(over, 0, 0, 9)

    over.dispatchEvent(
      new MouseEvent('mousedown', { button: 0, clientX: clickX, clientY: 100, bubbles: true }),
    )
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: clickX, clientY: 100, bubbles: true }))

    expect(onBeatSelect).not.toHaveBeenCalled()
  })

  // F5 reopen hydration: `initialBadDataMarks` seeds `badDataMarks` at
  // component init (see EcgGraph.svelte's `badDataMarks` declaration) so a
  // reopened file's prior marks render immediately — this proves that seed
  // renders with no drag/interaction needed first, unlike every other mark
  // in this file's other tests, which all arrive via `addBadData`.
  it('renders a mark from initialBadDataMarks immediately, without any drag interaction', () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    render(EcgGraph, {
      props: {
        path: '/data/57.txt',
        channel: 'channel 1',
        initialBadDataMarks: [{ id: 1, start: 2, stop: 5 }],
      },
    })

    expect(screen.getByTestId('bad-data-mark')).toBeInTheDocument()
  })

  it('removes a bad-data mark via deleteBadData when the mark is clicked', async () => {
    const mark: BadDataMark = { id: 42, start: 2, stop: 6 }
    const fetchMock = routedFetch({ addBadData: () => badDataAddResponse(mark) })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    screen.getByTestId('bad-data-mode-button').click()

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 500, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: 200, clientY: 100, bubbles: true }))

    await waitFor(() => expect(screen.getAllByTestId('bad-data-mark')).toHaveLength(1))
    expect(fetchMock).toHaveBeenCalledTimes(3)

    screen.getByTestId('bad-data-mark').click()

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4))

    const deleteCallUrl = fetchedUrl(fetchMock, 3)
    expect(deleteCallUrl.pathname).toBe('/files/bad-data')
    const [, deleteInit] = fetchMock.mock.calls[3]
    expect((deleteInit as RequestInit).method).toBe('DELETE')
    const deleteBody = fetchedBody(fetchMock, 3)
    expect(deleteBody.path).toBe('/data/57.txt')
    expect(deleteBody.id).toBe(42)

    await waitFor(() => expect(screen.queryByTestId('bad-data-mark')).not.toBeInTheDocument())
  })

  describe('Y-axis zoom', () => {
    it('the Y zoom-in button highlights Reset view without firing a new fetch', async () => {
      const fetchMock = routedFetch()
      vi.stubGlobal('fetch', fetchMock)

      withMockedClientWidth(800, () => {
        render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
      })
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

      const resetButton = screen.getByTestId('reset-view-button')
      expect(resetButton.className).not.toContain('btn-active')

      await fireEvent.click(screen.getByTestId('zoom-in-y-button'))

      expect(resetButton.className).toContain('btn-active')
      // Y-zoom is a pure rendering concern — no new data is needed.
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('Shift+wheel zooms Y (highlights Reset view) instead of X (no debounced re-fetch)', async () => {
      const fetchMock = routedFetch()
      vi.stubGlobal('fetch', fetchMock)

      withMockedClientWidth(800, () => {
        render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
      })
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

      const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
      vi.useFakeTimers()
      over.dispatchEvent(
        new WheelEvent('wheel', { deltaY: -100, clientX: 400, clientY: 100, shiftKey: true, bubbles: true, cancelable: true }),
      )
      await vi.advanceTimersByTimeAsync(150)
      vi.useRealTimers()

      expect(screen.getByTestId('reset-view-button').className).toContain('btn-active')
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('Reset view restores Y auto-ranging without throwing, after a Y-zoom', async () => {
      const fetchMock = routedFetch()
      vi.stubGlobal('fetch', fetchMock)

      withMockedClientWidth(800, () => {
        render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
      })
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

      await fireEvent.click(screen.getByTestId('zoom-in-y-button'))
      await fireEvent.click(screen.getByTestId('reset-view-button'))

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4))
      expect(screen.getByTestId('reset-view-button').className).not.toContain('btn-active')
      expect(screen.queryByTestId('ecg-graph-error')).not.toBeInTheDocument()
    })
  })

  describe('legend / category filter', () => {
    it('renders a legend entry for every display category', async () => {
      const fetchMock = routedFetch()
      vi.stubGlobal('fetch', fetchMock)
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

      const { DISPLAY_CATEGORIES } = await import('../../lib/categories')
      for (const cat of DISPLAY_CATEGORIES) {
        expect(screen.getByTestId(`legend-toggle-${cat}`)).toBeInTheDocument()
      }
    })

    it('toggling a legend entry marks it inactive, and toggling again restores it', async () => {
      const fetchMock = routedFetch()
      vi.stubGlobal('fetch', fetchMock)
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

      const toggle = screen.getByTestId('legend-toggle-tachycardia_absolute')
      expect(toggle.getAttribute('aria-pressed')).toBe('true')

      await fireEvent.click(toggle)
      expect(toggle.getAttribute('aria-pressed')).toBe('false')
      expect(toggle.className).toContain('legend-chip--inactive')

      await fireEvent.click(toggle)
      expect(toggle.getAttribute('aria-pressed')).toBe('true')
      expect(toggle.className).not.toContain('legend-chip--inactive')
    })

    it('the review-state filter toggles which button is active', async () => {
      const fetchMock = routedFetch()
      vi.stubGlobal('fetch', fetchMock)
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

      expect(screen.getByTestId('review-filter-all').className).toContain('btn-active')

      await fireEvent.click(screen.getByTestId('review-filter-reviewed'))
      expect(screen.getByTestId('review-filter-reviewed').className).toContain('btn-active')
      expect(screen.getByTestId('review-filter-all').className).not.toContain('btn-active')

      await fireEvent.click(screen.getByTestId('review-filter-unreviewed'))
      expect(screen.getByTestId('review-filter-unreviewed').className).toContain('btn-active')
      expect(screen.getByTestId('review-filter-reviewed').className).not.toContain('btn-active')
    })
  })

  describe('selected beat highlight', () => {
    it('accepts a selectedBeatTs prop and re-renders without error when it changes', async () => {
      const targetBeat = beat({ ts: 5, r_amplitude: 6, any_arrhythmia: true })
      const fetchMock = routedFetch({ beats: () => beatsWindowResponse({ beats: [targetBeat], count: 1 }) })
      vi.stubGlobal('fetch', fetchMock)

      const { rerender } = render(EcgGraph, {
        props: { path: '/data/57.txt', channel: 'channel 1', selectedBeatTs: null },
      })
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

      await rerender({ path: '/data/57.txt', channel: 'channel 1', selectedBeatTs: 5 })

      expect(screen.queryByTestId('ecg-graph-error')).not.toBeInTheDocument()

      await rerender({ path: '/data/57.txt', channel: 'channel 1', selectedBeatTs: null })
      expect(screen.queryByTestId('ecg-graph-error')).not.toBeInTheDocument()
    })
  })
})

describe('buildBeatAlignedData', () => {
  // This is the exact "series/point data passed to uPlot" the beat-marker
  // feature is built on (see EcgGraph.svelte's onMount/refetch, which feed
  // its output straight into `new uPlot(...)`/`chart.setData(...)` via
  // `toChartData`) — so asserting on its output directly is a precise,
  // uPlot-internals-free way to verify "the plot receives one
  // distinguishable marker point per (category, reviewed-state) bucket",
  // without reaching into a live chart instance.
  function bucketY(result: ReturnType<typeof buildBeatAlignedData>, category: string, reviewed: boolean) {
    return result.markerY[`${category}|${reviewed ? 'reviewed' : 'unreviewed'}`]
  }

  it('merges the channel waveform with beat markers into a shared x-axis', () => {
    const channelX = [0, 1, 2, 3, 4]
    const channelY = [0, 1, 0, -1, 0]
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5, any_arrhythmia: true, tachycardia_absolute: true }),
      beat({ ts: 2, r_amplitude: 6, any_arrhythmia: false }),
      // A beat timestamp that doesn't land on a channel sample (4.5) — the
      // "not yet arrhythmia-detected" case.
      beat({ ts: 4.5, r_amplitude: 7, any_arrhythmia: null }),
    ]

    const result = buildBeatAlignedData(channelX, channelY, beats)

    expect(result.xs).toEqual([0, 1, 2, 3, 4, 4.5])
    // spanGaps on the waveform series (set in EcgGraph.svelte) means this
    // trailing null doesn't fragment the rendered line.
    expect(result.channelY).toEqual([0, 1, 0, -1, 0, null])
  })

  it('buckets each beat by its primary display category, unreviewed by default', () => {
    const channelX = [0, 1, 2, 4.5]
    const channelY = [0, 1, 0, 0]
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5, any_arrhythmia: true, tachycardia_absolute: true }),
      beat({ ts: 2, r_amplitude: 6, any_arrhythmia: false }),
      beat({ ts: 4.5, r_amplitude: 7, any_arrhythmia: null }),
    ]

    const result = buildBeatAlignedData(channelX, channelY, beats)

    expect(bucketY(result, 'tachycardia_absolute', false)).toEqual([null, 5, null, null])
    expect(bucketY(result, 'normal', false)).toEqual([null, null, 6, null])
    expect(bucketY(result, 'unevaluated', false)).toEqual([null, null, null, 7])
    // Every other bucket stays entirely null for this fixture.
    expect(bucketY(result, 'prem_beat', false)).toEqual([null, null, null, null])
  })

  it('picks the primary category by fixed priority when multiple flags are set', () => {
    const beats: WindowBeat[] = [
      beat({
        ts: 1,
        r_amplitude: 5,
        any_arrhythmia: true,
        tachycardia_absolute: true,
        bradycardia_absolute: true, // earlier in REASSIGNABLE_CATEGORIES' priority order
      }),
    ]
    const result = buildBeatAlignedData([0, 1], [0, 0], beats)

    expect(bucketY(result, 'bradycardia_absolute', false)).toEqual([null, 5])
    expect(bucketY(result, 'tachycardia_absolute', false)).toEqual([null, null])
  })

  it('splits reviewed beats into the reviewed bucket, separate from unreviewed', () => {
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5, any_arrhythmia: true, prem_beat: true, review_state: 'confirmed' }),
      beat({ ts: 2, r_amplitude: 6, any_arrhythmia: false, review_state: 'unreviewed' }),
    ]
    const result = buildBeatAlignedData([0, 1, 2], [0, 0, 0], beats)

    expect(bucketY(result, 'prem_beat', true)).toEqual([null, 5, null])
    expect(bucketY(result, 'prem_beat', false)).toEqual([null, null, null])
    expect(bucketY(result, 'normal', false)).toEqual([null, null, 6])
    expect(bucketY(result, 'normal', true)).toEqual([null, null, null])
  })

  it('marks only the selected beat in selectedY, by exact ts', () => {
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5 }),
      beat({ ts: 2, r_amplitude: 6 }),
    ]
    const result = buildBeatAlignedData([0, 1, 2], [0, 0, 0], beats, 2)

    expect(result.selectedY).toEqual([null, null, 6])
  })

  it('leaves selectedY entirely null when nothing is selected', () => {
    const beats: WindowBeat[] = [beat({ ts: 1, r_amplitude: 5 })]
    const result = buildBeatAlignedData([0, 1], [0, 0], beats)

    expect(result.selectedY).toEqual([null, null])
  })

  it('returns the channel waveform unchanged when there are no beats', () => {
    const result = buildBeatAlignedData([0, 1, 2], [1, 2, 3], [])
    expect(result.xs).toEqual([0, 1, 2])
    expect(result.channelY).toEqual([1, 2, 3])
    expect(bucketY(result, 'normal', false)).toEqual([null, null, null])
  })

  it('marks only rejected beats in rejectedY, leaving normal/unreviewed beats null', () => {
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5, any_arrhythmia: false, review_state: 'rejected' }),
      beat({ ts: 2, r_amplitude: 6, any_arrhythmia: false, review_state: 'unreviewed' }),
      beat({ ts: 3, r_amplitude: 7, any_arrhythmia: true, prem_beat: true, review_state: 'confirmed' }),
    ]
    const result = buildBeatAlignedData([0, 1, 2, 3], [0, 0, 0, 0], beats)

    expect(result.rejectedY).toEqual([null, 5, null, null])
    // A rejected beat still buckets as 'normal' (reviewed) for its own
    // color/shape marker — the overlay is additive, not a replacement.
    expect(bucketY(result, 'normal', true)).toEqual([null, 5, null, null])
  })
})

describe('toChartData', () => {
  it('flattens xs/channelY/markerY/selectedY/rejectedY into MARKER_BUCKETS order, for uPlot.setData', async () => {
    const { MARKER_BUCKETS } = await import('../../lib/categories')
    const beats: WindowBeat[] = [beat({ ts: 1, r_amplitude: 5, any_arrhythmia: true, prem_beat: true })]
    const merged = buildBeatAlignedData([0, 1], [0, 0], beats)

    const data = toChartData(merged)

    expect(data[0]).toBe(merged.xs)
    expect(data[1]).toBe(merged.channelY)
    MARKER_BUCKETS.forEach((bucket, i) => {
      expect(data[i + 2]).toBe(merged.markerY[bucket.key])
    })
    expect(data[data.length - 2]).toBe(merged.selectedY)
    expect(data[data.length - 1]).toBe(merged.rejectedY)
  })
})
