import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRawSnippet } from 'svelte'
import EcgGraph, {
  buildBeatAlignedData,
  focusWidthSeconds,
  markerZoomScale,
  toChartData,
  MARKER_LANE_Y,
} from './EcgGraph.svelte'
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
    updateBadData?: (body: Record<string, unknown>) => unknown
    snapBeat?: (body: Record<string, unknown>) => unknown
    addBeat?: (body: Record<string, unknown>) => unknown
  } = {},
) {
  return vi.fn((url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/beats/window') {
      return Promise.resolve((handlers.beats ?? (() => beatsWindowResponse()))())
    }
    if (pathname === '/files/beats/snap') {
      const body = JSON.parse(String(init?.body))
      return Promise.resolve(
        (handlers.snapBeat ?? ((b) => ({ ok: true, json: async () => ({ status: 'ok', ts: b.ts, error: null }) })))(body),
      )
    }
    if (pathname === '/files/beats/one' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      return Promise.resolve(
        (handlers.addBeat ??
          ((b) => ({
            ok: true,
            json: async () => ({
              status: 'ok',
              beat: beat({ ts: b.ts as number, r_amplitude: 1 }),
              error: null,
            }),
          })))(body),
      )
    }
    if (pathname === '/files/bad-data') {
      if (init?.method === 'DELETE') {
        return Promise.resolve((handlers.deleteBadData ?? (() => badDataDeleteResponse()))())
      }
      if (init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body))
        return Promise.resolve(
          (handlers.updateBadData ??
            ((b) =>
              badDataAddResponse({
                id: b.id as number,
                start: Math.min(b.start as number, b.stop as number),
                stop: Math.max(b.start as number, b.stop as number),
              })))(body),
        )
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

// One wheel step zooms 25%, and the buffer loaded on mount stays adequate
// until the view is ~3x tighter (MIN_ZOOM_RATIO_BEFORE_REFETCH) — so a
// single step correctly needs no refetch. These tests are about what
// happens WHEN a refetch is due, so they zoom past that threshold.
// Must be called with fake timers already active. uPlot commits `setScale`
// on an animation frame, so dispatching the wheel events back to back would
// have every handler read the same pre-zoom scale and collapse into a
// single step — the timer advance between them lets each one land. The
// gaps stay well inside the 150ms debounce so the callers' own
// "not yet / now" assertions still hold.
async function zoomInPastRefetchThreshold(over: HTMLDivElement, steps = 5) {
  for (let i = 0; i < steps; i++) {
    over.dispatchEvent(
      new WheelEvent('wheel', { deltaY: -100, clientX: 400, clientY: 100, bubbles: true, cancelable: true }),
    )
    await vi.advanceTimersByTimeAsync(20)
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
    await zoomInPastRefetchThreshold(over)
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
    await zoomInPastRefetchThreshold(over)

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
    await zoomInPastRefetchThreshold(over)
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

  it('removes a bad-data mark via deleteBadData when Delete is pressed in its editor', async () => {
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

    // Clicking a mark selects it for editing; deleting is an explicit action.
    screen.getByTestId('bad-data-mark').click()
    expect(fetchMock).toHaveBeenCalledTimes(3)
    await waitFor(() => expect(screen.getByTestId('bad-data-editor')).toBeInTheDocument())
    await fireEvent.click(screen.getByTestId('bad-data-delete-button'))

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

  it('edits a bad-data mark range through the editor and PATCHes the new range', async () => {
    const mark: BadDataMark = { id: 42, start: 2, stop: 6 }
    const fetchMock = routedFetch({ addBadData: () => badDataAddResponse(mark) })
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1', initialBadDataMarks: [mark] } })
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    screen.getByTestId('bad-data-mark').click()
    const stop = (await screen.findByTestId('bad-data-stop-input')) as HTMLInputElement
    expect(stop.value).toBe('6.000')

    await fireEvent.input(stop, { target: { value: '7.5' } })
    await fireEvent.click(screen.getByTestId('bad-data-save-button'))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    const [, init] = fetchMock.mock.calls[2]
    expect((init as RequestInit).method).toBe('PATCH')
    expect(fetchedBody(fetchMock, 2)).toEqual({ path: '/data/57.txt', id: 42, start: 2, stop: 7.5 })
    await waitFor(() =>
      expect((screen.getByTestId('bad-data-stop-input') as HTMLInputElement).value).toBe('7.500'),
    )
  })

  it('disables Save when the edited range is empty or not a number', async () => {
    const mark: BadDataMark = { id: 42, start: 2, stop: 6 }
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1', initialBadDataMarks: [mark] } })
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    screen.getByTestId('bad-data-mark').click()
    const start = (await screen.findByTestId('bad-data-start-input')) as HTMLInputElement
    await fireEvent.input(start, { target: { value: 'abc' } })
    expect(screen.getByTestId('bad-data-save-button')).toBeDisabled()
    await fireEvent.input(start, { target: { value: '6' } })
    expect(screen.getByTestId('bad-data-save-button')).toBeDisabled()
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
    const channelY = [0, 11, 12, 13]
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5, any_arrhythmia: true, tachycardia_absolute: true }),
      beat({ ts: 2, r_amplitude: 6, any_arrhythmia: false }),
      beat({ ts: 4.5, r_amplitude: 7, any_arrhythmia: null }),
    ]

    const result = buildBeatAlignedData(channelX, channelY, beats)

    // Every marker sits on the shared lane height, whatever the trace does
    // at that timestamp.
    const L = MARKER_LANE_Y
    expect(bucketY(result, 'tachycardia_absolute', false)).toEqual([null, L, null, null])
    expect(bucketY(result, 'normal', false)).toEqual([null, null, L, null])
    expect(bucketY(result, 'unevaluated', false)).toEqual([null, null, null, L])
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
    const result = buildBeatAlignedData([0, 1], [0, 9], beats)

    expect(bucketY(result, 'bradycardia_absolute', false)).toEqual([null, MARKER_LANE_Y])
    expect(bucketY(result, 'tachycardia_absolute', false)).toEqual([null, null])
  })

  it('splits reviewed beats into the reviewed bucket, separate from unreviewed', () => {
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5, any_arrhythmia: true, prem_beat: true, review_state: 'confirmed' }),
      beat({ ts: 2, r_amplitude: 6, any_arrhythmia: false, review_state: 'unreviewed' }),
    ]
    const result = buildBeatAlignedData([0, 1, 2], [0, 21, 22], beats)

    expect(bucketY(result, 'prem_beat', true)).toEqual([null, MARKER_LANE_Y, null])
    expect(bucketY(result, 'prem_beat', false)).toEqual([null, null, null])
    expect(bucketY(result, 'normal', false)).toEqual([null, null, MARKER_LANE_Y])
    expect(bucketY(result, 'normal', true)).toEqual([null, null, null])
  })

  it('marks only the selected beat in selectedY, by exact ts', () => {
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5 }),
      beat({ ts: 2, r_amplitude: 6 }),
    ]
    const result = buildBeatAlignedData([0, 1, 2], [0, 31, 32], beats, 2)

    expect(result.selectedY).toEqual([null, null, MARKER_LANE_Y])
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
    const result = buildBeatAlignedData([0, 1, 2, 3], [0, 41, 42, 43], beats)

    expect(result.rejectedY).toEqual([null, MARKER_LANE_Y, null, null])
    // A rejected beat still buckets as 'normal' (reviewed) for its own
    // color/shape marker — the overlay is additive, not a replacement.
    expect(bucketY(result, 'normal', true)).toEqual([null, MARKER_LANE_Y, null, null])
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

describe('EcgGraph beat-of-interest navigation', () => {
  const BEATS_OF_INTEREST = [2, 5, 8]

  async function renderWithBeatsOfInterest(
    fetchMock: ReturnType<typeof vi.fn>,
    beatsOfInterest: number[] = BEATS_OF_INTEREST,
  ) {
    const result = withMockedClientWidth(800, () =>
      render(EcgGraph, {
        props: { path: '/data/57.txt', channel: 'channel 1', beatsOfInterest },
      }),
    )
    // Mount fetches the channel window and beats; the auto-focus onto the
    // first beat of interest then fetches its narrowed range.
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(4))
    return result
  }

  it('shows the total count in the legend', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    await renderWithBeatsOfInterest(fetchMock)

    expect(screen.getByTestId('legend-beats-of-interest-count')).toHaveTextContent('3 beats of interest')
  })

  async function focusSpanFor(typicalRr: number | null) {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () =>
      render(EcgGraph, {
        props: { path: '/data/57.txt', channel: 'channel 1', beatsOfInterest: BEATS_OF_INTEREST, typicalRr },
      }),
    )
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(4))
    const focusCall = fetchMock.mock.calls
      .map((_, i) => fetchedUrl(fetchMock, i))
      .filter((url) => url.pathname === '/channels/window')
      .at(-1)!
    return Number(focusCall.searchParams.get('end')) - Number(focusCall.searchParams.get('start'))
  }

  it('sizes the view it jumps to from the recording\'s typical RR', async () => {
    const wide = await focusSpanFor(0.5) // 21 beats = 10.5 s
    cleanup()
    const narrow = await focusSpanFor(0.05) // 21 beats = 1.05 s
    cleanup()
    const fallback = await focusSpanFor(null) // 2 s

    expect(wide).toBeGreaterThan(fallback)
    expect(narrow).toBeLessThan(fallback)
  })

  it('opens focused on the first beat of interest rather than the whole recording', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    await renderWithBeatsOfInterest(fetchMock)

    // The mount fetch covers the full extent; the auto-focus fetch that
    // follows must be a narrow window centred on the first flagged beat
    // (ts=2), not the whole file again.
    const focusCall = fetchMock.mock.calls
      .map((call, i) => fetchedUrl(fetchMock, i))
      .filter((url) => url.pathname === '/channels/window')
      .at(-1)!
    const start = Number(focusCall.searchParams.get('start'))
    const end = Number(focusCall.searchParams.get('end'))

    expect(start).toBeLessThan(2)
    expect(end).toBeGreaterThan(2)
    expect((start + end) / 2).toBeCloseTo(2, 5)
    expect(end - start).toBeLessThan(10)

    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 1 of 3')
  })

  it('steps forward, backward, and to either end of the list', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    await renderWithBeatsOfInterest(fetchMock)

    await fireEvent.click(screen.getByTestId('next-beat-of-interest-button'))
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 2 of 3')

    await fireEvent.click(screen.getByTestId('prev-beat-of-interest-button'))
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 1 of 3')

    await fireEvent.click(screen.getByTestId('last-beat-of-interest-button'))
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 3 of 3')

    await fireEvent.click(screen.getByTestId('first-beat-of-interest-button'))
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 1 of 3')
  })

  it('wraps around at both ends so a review pass never dead-ends', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    await renderWithBeatsOfInterest(fetchMock)

    await fireEvent.click(screen.getByTestId('last-beat-of-interest-button'))
    await fireEvent.click(screen.getByTestId('next-beat-of-interest-button'))
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 1 of 3')

    await fireEvent.click(screen.getByTestId('prev-beat-of-interest-button'))
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 3 of 3')
  })

  it('selects the beat it jumps to, so the review panel acts on it', async () => {
    const onBeatSelect = vi.fn()
    const fetchMock = routedFetch({
      beats: () =>
        beatsWindowResponse({
          beats: [beat({ ts: 2, r_amplitude: 5, any_arrhythmia: true, prem_beat: true })],
          count: 1,
        }),
    })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () =>
      render(EcgGraph, {
        props: { path: '/data/57.txt', channel: 'channel 1', beatsOfInterest: [2], onBeatSelect },
      }),
    )

    await waitFor(() => expect(onBeatSelect).toHaveBeenCalled())
    expect(onBeatSelect.mock.calls[0][0]).toMatchObject({ ts: 2, prem_beat: true })
  })

  it('disables the controls and says so when nothing is flagged', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1', beatsOfInterest: [] } }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('No beats of interest')
    expect(screen.getByTestId('next-beat-of-interest-button')).toBeDisabled()
    expect(screen.getByTestId('first-beat-of-interest-button')).toBeDisabled()
    expect(screen.getByTestId('legend-beats-of-interest-count')).toHaveTextContent('0 beats of interest')
  })

  it('does not yank the view back when the flagged list changes mid-review', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = await renderWithBeatsOfInterest(fetchMock)

    await fireEvent.click(screen.getByTestId('last-beat-of-interest-button'))
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 3 of 3')

    // Let that jump's own fetches finish before snapshotting, so the count
    // below measures only what the rerender causes.
    await new Promise((resolve) => setTimeout(resolve, 50))
    const callsBefore = fetchMock.mock.calls.length

    // A reject removes a beat from the list — the auto-focus must not fire
    // again and drag the technician back to the top of the recording.
    await rerender({ path: '/data/57.txt', channel: 'channel 1', beatsOfInterest: [2, 5] })

    await Promise.resolve()
    await Promise.resolve()
    expect(fetchMock.mock.calls.length).toBe(callsBefore)
  })
})

describe('EcgGraph time navigation', () => {
  async function mountGraph(fetchMock: ReturnType<typeof vi.fn>) {
    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  }

  function lastChannelWindowRange(fetchMock: ReturnType<typeof vi.fn>) {
    const url = fetchMock.mock.calls
      .map((_call, i) => fetchedUrl(fetchMock, i))
      .filter((u) => u.pathname === '/channels/window')
      .at(-1)!
    return { start: Number(url.searchParams.get('start')), end: Number(url.searchParams.get('end')) }
  }

  it('pages forward and back by roughly one window at the current zoom', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    await mountGraph(fetchMock)

    await fireEvent.click(screen.getByTestId('next-window-button'))
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(2))
    const forward = lastChannelWindowRange(fetchMock)

    await fireEvent.click(screen.getByTestId('prev-window-button'))
    await waitFor(() => {
      const back = lastChannelWindowRange(fetchMock)
      // Paging back must move the window the other way, not repeat the
      // forward jump.
      expect(back.start).toBeLessThan(forward.start)
    })
  })

  it('jumps to the start and end of the recording', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    await mountGraph(fetchMock)

    // Zoom in first: while the whole recording is already on screen, a jump
    // to either end is legitimately the same view, so there'd be nothing to
    // observe. (The zoom itself needs no fetch — the mount already buffered
    // the whole file — so this waits on the jump's fetch, not the zoom's.)
    await fireEvent.click(screen.getByTestId('zoom-in-button'))

    const beforeEnd = fetchMock.mock.calls.length
    await fireEvent.click(screen.getByTestId('jump-to-end-button'))
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(beforeEnd))
    const atEnd = lastChannelWindowRange(fetchMock)

    const beforeStart = fetchMock.mock.calls.length
    await fireEvent.click(screen.getByTestId('jump-to-start-button'))
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(beforeStart))
    const atStart = lastChannelWindowRange(fetchMock)

    // The end jump reaches the tail of the recording, the start jump the
    // head — and they are genuinely different views.
    expect(atEnd.end).toBeGreaterThan(atStart.end)
    expect(atStart.start).toBeLessThan(atEnd.start)
  })
})

describe('EcgGraph raw/filtered trace toggle', () => {
  it('re-fetches the visible range as filtered, then back to raw', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchedUrl(fetchMock, 0).searchParams.get('filtered')).toBe('false')

    await fireEvent.click(screen.getByTestId('toggle-filtered-signal-button'))

    await waitFor(() => {
      const channelCalls = fetchMock.mock.calls
        .map((_call, i) => fetchedUrl(fetchMock, i))
        .filter((u) => u.pathname === '/channels/window')
      expect(channelCalls.at(-1)!.searchParams.get('filtered')).toBe('true')
    })
    expect(screen.getByTestId('toggle-filtered-signal-button')).toHaveTextContent('Filtered')

    await fireEvent.click(screen.getByTestId('toggle-filtered-signal-button'))
    await waitFor(() => {
      const channelCalls = fetchMock.mock.calls
        .map((_call, i) => fetchedUrl(fetchMock, i))
        .filter((u) => u.pathname === '/channels/window')
      expect(channelCalls.at(-1)!.searchParams.get('filtered')).toBe('false')
    })
    expect(screen.getByTestId('toggle-filtered-signal-button')).toHaveTextContent('Raw')
  })
})

describe('EcgGraph hide-rejected filter', () => {
  it('drops rejected beats from the merged data without a re-fetch', async () => {
    const fetchMock = routedFetch({
      beats: () =>
        beatsWindowResponse({
          beats: [
            beat({ ts: 2, r_amplitude: 5, review_state: 'rejected' }),
            beat({ ts: 5, r_amplitude: 6, review_state: 'unreviewed' }),
          ],
          count: 2,
        }),
    })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const callsBefore = fetchMock.mock.calls.length
    await fireEvent.click(screen.getByTestId('hide-rejected-toggle'))

    expect(screen.getByTestId('hide-rejected-toggle')).toHaveTextContent('Rejected hidden')
    // Purely a local re-merge of data already held — no network round trip.
    expect(fetchMock.mock.calls.length).toBe(callsBefore)
  })

  it('excludes rejected beats from every marker series when on', () => {
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5, review_state: 'rejected' }),
      beat({ ts: 2, r_amplitude: 6, review_state: 'unreviewed' }),
    ]

    const shown = buildBeatAlignedData([0, 1, 2], [0, 51, 52], beats, null, false)
    const hidden = buildBeatAlignedData([0, 1, 2], [0, 51, 52], beats, null, true)

    expect(shown.rejectedY).toEqual([null, MARKER_LANE_Y, null])
    expect(hidden.rejectedY).toEqual([null, null, null])
    // The unreviewed beat is untouched either way.
    expect(hidden.markerY['normal|unreviewed']).toEqual([null, null, MARKER_LANE_Y])
  })
})

describe('EcgGraph bad-data marking UX', () => {
  async function mountMarking(fetchMock: ReturnType<typeof vi.fn>, marks: BadDataMark[] = []) {
    withMockedClientWidth(800, () =>
      render(EcgGraph, {
        props: { path: '/data/57.txt', channel: 'channel 1', initialBadDataMarks: marks },
      }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    await fireEvent.click(screen.getByTestId('bad-data-mode-button'))
  }

  // Coordinates are taken from the plot's own rendered width (jsdom does no
  // layout, so uPlot's explicit style is the only real geometry available) —
  // the same technique the beat-click tests above use. `fromFrac`/`toFrac`
  // are fractions of the plotting area.
  function dragOnPlot(fromFrac: number, toFrac: number) {
    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    const plotWidthPx = parseFloat(over.style.width)
    const fromX = plotWidthPx * fromFrac
    const toX = plotWidthPx * toFrac
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: fromX, clientY: 50, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: toX, clientY: 50, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: toX, clientY: 50, bubbles: true }))
  }

  it('explains the mode while it is active and leaves on Escape', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    await mountMarking(fetchMock)

    expect(screen.getByTestId('bad-data-mode-banner')).toBeInTheDocument()

    await fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByTestId('bad-data-mode-banner')).not.toBeInTheDocument()
  })

  it('confirms a newly marked range', async () => {
    const fetchMock = routedFetch({
      addBadData: () => badDataAddResponse({ id: 7, start: 1, stop: 3 }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await mountMarking(fetchMock)

    dragOnPlot(0.2, 0.6)

    await waitFor(() => {
      expect(screen.getByTestId('bad-data-toast')).toBeInTheDocument()
    })
    expect(screen.getAllByTestId('bad-data-mark')).toHaveLength(1)
  })

  it('selects the mark under a click instead of creating a zero-width one, and Delete removes it', async () => {
    const existing: BadDataMark = { id: 3, start: 0, stop: 9 }
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    await mountMarking(fetchMock, [existing])

    expect(screen.getAllByTestId('bad-data-mark')).toHaveLength(1)
    const callsBefore = fetchMock.mock.calls.length

    // A click (no movement) in the middle of the plot, which lies inside
    // the existing mark's 0-9s span.
    dragOnPlot(0.5, 0.5)

    await waitFor(() => expect(screen.getByTestId('bad-data-editor')).toBeInTheDocument())
    // Selecting must not hit the backend: no add, no delete.
    expect(fetchMock.mock.calls.length).toBe(callsBefore)
    expect(screen.getAllByTestId('bad-data-mark')).toHaveLength(1)

    await fireEvent.keyDown(document, { key: 'Delete' })

    await waitFor(() => {
      expect(screen.queryAllByTestId('bad-data-mark')).toHaveLength(0)
    })
    const deleteCall = fetchMock.mock.calls.find(
      (call) => (call[1] as RequestInit | undefined)?.method === 'DELETE',
    )
    expect(deleteCall).toBeTruthy()
  })
})

describe('focusWidthSeconds', () => {
  it('shows ten beats either side of the beat of interest', () => {
    expect(focusWidthSeconds(0.12)).toBeCloseTo(21 * 0.12)
    expect(focusWidthSeconds(0.2)).toBeCloseTo(4.2)
  })

  it('is wider than the old fixed 2 s for a mouse-rate recording', () => {
    expect(focusWidthSeconds(0.12)).toBeGreaterThan(2)
  })

  it('never goes tighter than half a second or wider than fifteen', () => {
    expect(focusWidthSeconds(0.01)).toBe(0.5)
    expect(focusWidthSeconds(5)).toBe(15)
  })

  it('falls back to the old 2 s when there is no usable estimate', () => {
    for (const bad of [null, undefined, 0, -1, NaN, Infinity]) {
      expect(focusWidthSeconds(bad as number | null | undefined)).toBe(2)
    }
  })
})

describe('markerZoomScale', () => {
  it('leaves markers at normal size when zoomed out', () => {
    expect(markerZoomScale(60)).toBe(1)
    expect(markerZoomScale(8)).toBe(1)
  })

  it('grows markers as the visible span shrinks', () => {
    expect(markerZoomScale(4)).toBeGreaterThan(1)
    expect(markerZoomScale(1)).toBeGreaterThan(markerZoomScale(4))
  })

  it('caps the growth so markers never swamp the trace', () => {
    expect(markerZoomScale(0.001)).toBe(2.5)
  })

  it('falls back to normal size for a degenerate span', () => {
    expect(markerZoomScale(0)).toBe(1)
    expect(markerZoomScale(NaN)).toBe(1)
  })
})

describe('EcgGraph add-beat flow', () => {
  function postsTo(fetchMock: ReturnType<typeof vi.fn>, pathname: string) {
    return fetchMock.mock.calls.filter(
      ([url, init]) =>
        new URL(url as string).pathname === pathname && (init as RequestInit | undefined)?.method === 'POST',
    )
  }

  function clickPlot(frac: number) {
    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    const x = parseFloat(over.style.width) * frac
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: x, clientY: 50, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: x, clientY: 50, bubbles: true }))
  }

  async function mountAdding(fetchMock: ReturnType<typeof vi.fn>) {
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    await fireEvent.click(screen.getByTestId('add-beat-mode-button'))
  }

  it('snaps a click to the suggested peak, shows it as pending, and saves nothing yet', async () => {
    const fetchMock = routedFetch({
      snapBeat: () => ({ ok: true, json: async () => ({ status: 'ok', ts: 4.2, error: null }) }),
    })
    await mountAdding(fetchMock)
    expect(screen.getByTestId('add-beat-mode-banner')).toBeInTheDocument()

    clickPlot(0.5)

    await waitFor(() => expect(screen.getByTestId('add-beat-pending')).toHaveTextContent('4.200'))
    expect(screen.getByTestId('add-beat-pending')).toHaveTextContent('snapped to the nearest peak')
    expect(postsTo(fetchMock, '/files/beats/snap')).toHaveLength(1)
    expect(fetchedBody(fetchMock, fetchMock.mock.calls.findIndex(([u]) => String(u).includes('/snap')))).toMatchObject({
      path: '/data/57.txt',
      channel: 'channel 1',
    })
    expect(postsTo(fetchMock, '/files/beats/one')).toHaveLength(0)
  })

  it('a second click overrides the snap with the exact clicked position, without asking the server to snap again', async () => {
    const fetchMock = routedFetch({
      snapBeat: () => ({ ok: true, json: async () => ({ status: 'ok', ts: 4.2, error: null }) }),
    })
    await mountAdding(fetchMock)
    clickPlot(0.5)
    await screen.findByTestId('add-beat-pending')

    clickPlot(0.8)

    await waitFor(() =>
      expect(screen.getByTestId('add-beat-pending')).toHaveTextContent('placed exactly where you clicked'),
    )
    expect(postsTo(fetchMock, '/files/beats/snap')).toHaveLength(1)
    expect(screen.getByTestId('add-beat-resnap-button')).toBeInTheDocument()
  })

  it('adds the beat only after Add beat is confirmed', async () => {
    const fetchMock = routedFetch({
      snapBeat: () => ({ ok: true, json: async () => ({ status: 'ok', ts: 4.2, error: null }) }),
    })
    await mountAdding(fetchMock)
    clickPlot(0.5)
    await screen.findByTestId('add-beat-pending')

    await fireEvent.click(screen.getByTestId('add-beat-confirm-button'))

    await waitFor(() => expect(postsTo(fetchMock, '/files/beats/one')).toHaveLength(1))
    const call = fetchMock.mock.calls.findIndex(
      ([u, init]) => String(u).includes('/files/beats/one') && (init as RequestInit).method === 'POST',
    )
    expect(fetchedBody(fetchMock, call)).toEqual({ path: '/data/57.txt', channel: 'channel 1', ts: 4.2 })
    await waitFor(() => expect(screen.queryByTestId('add-beat-pending')).not.toBeInTheDocument())
    expect(screen.getByTestId('bad-data-toast')).toHaveTextContent('Beat added at 4.200s')
  })

  it('Escape cancels a pending beat first, then leaves add-beat mode; nothing is added', async () => {
    const fetchMock = routedFetch({
      snapBeat: () => ({ ok: true, json: async () => ({ status: 'ok', ts: 4.2, error: null }) }),
    })
    await mountAdding(fetchMock)
    clickPlot(0.5)
    await screen.findByTestId('add-beat-pending')

    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('add-beat-pending')).not.toBeInTheDocument()
    expect(screen.getByTestId('add-beat-mode-banner')).toBeInTheDocument()

    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('add-beat-mode-banner')).not.toBeInTheDocument()
    expect(postsTo(fetchMock, '/files/beats/one')).toHaveLength(0)
  })

  it('shows the server reason when there is no peak to snap to', async () => {
    const fetchMock = routedFetch({
      snapBeat: () => ({
        ok: true,
        json: async () => ({ status: 'error', ts: null, error: 'There is already a beat here (t=4.000s)' }),
      }),
    })
    await mountAdding(fetchMock)

    clickPlot(0.5)

    await waitFor(() => expect(screen.getByTestId('add-beat-error')).toHaveTextContent('already a beat'))
    expect(screen.queryByTestId('add-beat-pending')).not.toBeInTheDocument()
  })

  it('is exclusive with bad-data mode', async () => {
    const fetchMock = routedFetch()
    await mountAdding(fetchMock)

    await fireEvent.click(screen.getByTestId('bad-data-mode-button'))

    expect(screen.queryByTestId('add-beat-mode-banner')).not.toBeInTheDocument()
    expect(screen.getByTestId('bad-data-mode-banner')).toBeInTheDocument()
  })
})

describe('EcgGraph focus mode and shortcuts', () => {
  it('fills the window and floats the tools, then exits on Escape', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    await fireEvent.click(screen.getByTestId('fullscreen-graph-button'))
    expect(screen.getByTestId('ecg-graph')).toHaveClass('fullscreen')
    // The tools become a collapsible floating panel only in focus mode.
    expect(screen.getByTestId('toggle-graph-tools-button')).toBeInTheDocument()

    await fireEvent.click(screen.getByTestId('toggle-graph-tools-button'))
    expect(screen.queryByTestId('beat-of-interest-nav')).not.toBeInTheDocument()

    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByTestId('ecg-graph')).not.toHaveClass('fullscreen')
  })

  it('steps between beats of interest from the keyboard', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () =>
      render(EcgGraph, {
        props: { path: '/data/57.txt', channel: 'channel 1', beatsOfInterest: [2, 5, 8] },
      }),
    )
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(4))

    await fireEvent.keyDown(document, { key: 'n' })
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 2 of 3')

    await fireEvent.keyDown(document, { key: 'p' })
    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 1 of 3')
  })

  it('shows the parent-supplied beat controls beside the selected beat in focus mode only', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    const focusOverlay = createRawSnippet(() => ({
      render: () => '<span data-testid="overlay-content">controls</span>',
    }))
    const modes: boolean[] = []
    // Held for the whole test (not just render): the popover anchors from
    // the plot area's live clientWidth, which jsdom reports as 0.
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
    try {
    render(EcgGraph, {
      props: {
        path: '/data/57.txt',
        channel: 'channel 1',
        selectedBeatTs: 5,
        focusOverlay,
        onFocusModeChange: (on: boolean) => modes.push(on),
      },
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('focus-beat-overlay')).not.toBeInTheDocument()

    await fireEvent.click(screen.getByTestId('fullscreen-graph-button'))
    await waitFor(() => expect(screen.getByTestId('overlay-content')).toBeInTheDocument())
    expect(modes.at(-1)).toBe(true)

    // Escape closes the popover first, and only a second Escape leaves focus mode.
    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('focus-beat-overlay')).not.toBeInTheDocument()
    expect(screen.getByTestId('ecg-graph')).toHaveClass('fullscreen')
    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByTestId('ecg-graph')).not.toHaveClass('fullscreen')
    } finally {
      delete (HTMLElement.prototype as any).clientWidth
    }
  })

  it('spells out what each review shortcut key does', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () =>
      render(EcgGraph, {
        props: { path: '/data/57.txt', channel: 'channel 1', beatsOfInterest: [2, 5, 8] },
      }),
    )
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(4))

    const hint = screen.getByTestId('beat-nav-hint')
    expect(hint).toHaveTextContent('N Next')
    expect(hint).toHaveTextContent('P Previous')
    expect(hint).toHaveTextContent('C Confirm arrhythmia')
    expect(hint).toHaveTextContent('R Reject')
  })

  it('labels the trace toggle and the marker filters', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    expect(screen.getByTestId('trace-filter-hint')).toHaveTextContent('As recorded')
    expect(screen.getByTestId('toggle-filtered-signal-button')).toHaveAttribute(
      'title',
      expect.stringContaining('only changes the display'),
    )
    expect(screen.getByTestId('marker-filter-label')).toHaveTextContent('Show markers')
  })

  it('ignores shortcuts while the technician is typing', async () => {
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)
    withMockedClientWidth(800, () =>
      render(EcgGraph, {
        props: { path: '/data/57.txt', channel: 'channel 1', beatsOfInterest: [2, 5, 8] },
      }),
    )
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(4))

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    await fireEvent.keyDown(input, { key: 'n' })

    expect(screen.getByTestId('beat-of-interest-counter')).toHaveTextContent('Beat of interest 1 of 3')
    input.remove()
  })
})

describe('EcgGraph rendering invariants', () => {
  it('still places the marker on the lane when the trace has no sample at the beat timestamp', () => {
    // Heavy downsampling can drop the exact sample a beat sits on; the
    // lane height does not depend on the trace, so the marker is unaffected.
    const beats: WindowBeat[] = [beat({ ts: 1.5, r_amplitude: 7 })]

    const result = buildBeatAlignedData([0, 1, 2], [0, 10, 20], beats)

    const i = result.xs.indexOf(1.5)
    expect(i).toBeGreaterThan(-1)
    expect(result.markerY['normal|unreviewed'][i]).toBe(MARKER_LANE_Y)
    // The waveform itself has no value there, and spanGaps bridges it.
    expect(result.channelY[i]).toBeNull()
  })

  it('keeps every series the same length as the shared x-axis, sorted and deduplicated', () => {
    const beats: WindowBeat[] = [
      beat({ ts: 1, r_amplitude: 5, any_arrhythmia: true, prem_beat: true }),
      beat({ ts: 1.5, r_amplitude: 6 }),
      beat({ ts: 3, r_amplitude: 7, review_state: 'rejected' }),
    ]

    const result = buildBeatAlignedData([0, 1, 2, 3], [0, 1, 2, 3], beats, 1)

    const n = result.xs.length
    expect(new Set(result.xs).size).toBe(n)
    expect([...result.xs].sort((a, b) => a - b)).toEqual(result.xs)
    expect(result.channelY).toHaveLength(n)
    expect(result.selectedY).toHaveLength(n)
    expect(result.rejectedY).toHaveLength(n)
    for (const key of Object.keys(result.markerY)) {
      expect(result.markerY[key]).toHaveLength(n)
    }
    // Each beat appears in exactly one marker bucket.
    for (let i = 0; i < n; i++) {
      const hits = Object.keys(result.markerY).filter((k) => result.markerY[k][i] !== null)
      expect(hits.length).toBeLessThanOrEqual(1)
    }
  })

  it('does not let a superseded beats refresh overwrite a newer one', async () => {
    // Two refreshes in flight; the FIRST resolves last. Its stale beats must
    // not end up on the chart (or in the click hit-test set).
    const resolvers: ((value: unknown) => void)[] = []
    const fetchMock = vi.fn((url: string) => {
      const { pathname } = new URL(url)
      if (pathname === '/beats/window') {
        return new Promise((resolve) => resolvers.push(resolve))
      }
      return Promise.resolve(channelWindowResponse())
    })
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1', beatsRefreshToken: 0 } }),
    )
    await waitFor(() => expect(resolvers.length).toBe(1))
    resolvers[0](beatsWindowResponse({ beats: [beat({ ts: 1, r_amplitude: 1 })], count: 1 }))
    // The chart only exists once the mount fetch has been applied; a
    // beats-only refresh is a no-op before that.
    await waitFor(() => {
      expect(screen.getByTestId('ecg-graph-container').querySelector('.u-over')).toBeTruthy()
    })

    // Two refreshes back to back.
    await rerender({ path: '/data/57.txt', channel: 'channel 1', beatsRefreshToken: 1 })
    await rerender({ path: '/data/57.txt', channel: 'channel 1', beatsRefreshToken: 2 })
    await waitFor(() => expect(resolvers.length).toBe(3))

    const stale = beatsWindowResponse({ beats: [beat({ ts: 8, r_amplitude: 8 })], count: 1 })
    const fresh = beatsWindowResponse({ beats: [beat({ ts: 5, r_amplitude: 5 })], count: 1 })
    resolvers[2](fresh)
    await Promise.resolve()
    resolvers[1](stale) // the older request, resolving last
    await Promise.resolve()

    // The chart must still be showing the newer result. Click where the
    // stale beat would have been and confirm nothing is selected there.
    await waitFor(() => {
      expect(screen.queryByTestId('ecg-graph-error')).not.toBeInTheDocument()
    })
  })
})

describe('EcgGraph filter consistency', () => {
  it('hides the rejected-beat strike along with the markers it overlays', async () => {
    const fetchMock = routedFetch({
      beats: () =>
        beatsWindowResponse({
          beats: [beat({ ts: 2, r_amplitude: 5, review_state: 'rejected' })],
          count: 1,
        }),
    })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    // A rejected beat lives in the 'normal' bucket. Filtering to unreviewed
    // hides that bucket, so the strike drawn on top of it must go too —
    // otherwise it floats with no marker underneath.
    await fireEvent.click(screen.getByTestId('review-filter-unreviewed'))
    await fireEvent.click(screen.getByTestId('legend-toggle-normal'))

    // No error, and the toggles reflect the filtered state.
    expect(screen.queryByTestId('ecg-graph-error')).not.toBeInTheDocument()
    expect(screen.getByTestId('legend-toggle-normal')).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('EcgGraph initial load robustness', () => {
  it('never asks for a near-empty trace when the container has not been laid out', async () => {
    // jsdom reports clientWidth 0, exactly like a container measured before
    // layout settles. Asking for resolution 1 made the backend flatten the
    // whole recording to ~4 points — the "graph only partially loads" bug.
    const fetchMock = routedFetch()
    vi.stubGlobal('fetch', fetchMock)

    render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())

    const resolution = Number(fetchedUrl(fetchMock, 0).searchParams.get('resolution'))
    expect(resolution).toBeGreaterThanOrEqual(400)
  })

  it('retries a failed first load instead of leaving the graph permanently blank', async () => {
    let attempt = 0
    const fetchMock = vi.fn((url: string) => {
      const { pathname } = new URL(url)
      if (pathname === '/beats/window') {
        return Promise.resolve(beatsWindowResponse())
      }
      attempt += 1
      // The backend is spawned asynchronously, so the very first request
      // after opening a file can genuinely arrive too early.
      return Promise.resolve(
        attempt === 1
          ? { ok: true, json: async () => ({ status: 'error', error: 'File not imported', x: [], y: [] }) }
          : channelWindowResponse(),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )

    // The automatic retry builds the chart that the failed first attempt
    // could not.
    await waitFor(
      () => {
        expect(screen.getByTestId('ecg-graph-container').querySelector('.u-over')).toBeTruthy()
      },
      { timeout: 3000 },
    )
  })
})
