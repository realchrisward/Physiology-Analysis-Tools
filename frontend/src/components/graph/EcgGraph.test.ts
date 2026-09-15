import { render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import EcgGraph from './EcgGraph.svelte'

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

describe('EcgGraph', () => {
  it('fetches the full range on mount and renders without error', async () => {
    const fetchMock = vi.fn().mockResolvedValue(channelWindowResponse())
    vi.stubGlobal('fetch', fetchMock)

    render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

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

  it('sends a resolution query param matching the container width', async () => {
    const fetchMock = vi.fn().mockResolvedValue(channelWindowResponse())
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    const url = fetchedUrl(fetchMock, 0)
    expect(url.searchParams.get('resolution')).toBe('800')
  })

  it('debounces a re-fetch after a pan/zoom interaction, with a different range', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(channelWindowResponse({ x: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] }))
      .mockResolvedValue(channelWindowResponse({ x: [3, 4, 5, 6], y: [-1, 0, 1, 0] }))
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    expect(over).toBeTruthy()

    vi.useFakeTimers()
    over.dispatchEvent(
      new WheelEvent('wheel', { deltaY: -100, clientX: 400, clientY: 100, bubbles: true, cancelable: true }),
    )

    // Not yet — still inside the 150ms debounce window.
    await vi.advanceTimersByTimeAsync(100)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(100)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    const firstCall = fetchedUrl(fetchMock, 0)
    const secondCall = fetchedUrl(fetchMock, 1)
    expect(secondCall.searchParams.get('start')).not.toBe(firstCall.searchParams.get('start'))
    expect(secondCall.searchParams.get('end')).not.toBe(firstCall.searchParams.get('end'))
  })

  it('restores the initial full extent on reset view, after a pan/zoom re-fetch', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(channelWindowResponse({ x: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] }))
      .mockResolvedValue(channelWindowResponse({ x: [3, 4, 5, 6], y: [-1, 0, 1, 0] }))
    vi.stubGlobal('fetch', fetchMock)

    withMockedClientWidth(800, () => {
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } })
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const initialCall = fetchedUrl(fetchMock, 0)

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement

    vi.useFakeTimers()
    over.dispatchEvent(
      new WheelEvent('wheel', { deltaY: -100, clientX: 400, clientY: 100, bubbles: true, cancelable: true }),
    )
    await vi.advanceTimersByTimeAsync(150)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    vi.useRealTimers()

    const resetButton = screen.getByTestId('reset-view-button')
    resetButton.click()

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    const resetCall = fetchedUrl(fetchMock, 2)
    expect(resetCall.searchParams.get('start')).toBe(initialCall.searchParams.get('start') ?? '0')
    // The initial mount requests an effectively-unbounded end
    // (Number.MAX_SAFE_INTEGER) to mean "everything"; the stored full
    // extent for Reset View is the *response's* actual x[0]/x[last]
    // (0 and 9 here), which is what a reset re-fetch should request.
    expect(resetCall.searchParams.get('end')).toBe('9')
  })

  it('removes document-level drag listeners on unmount, so a stale mid-drag mouseup fetches nothing', async () => {
    const fetchMock = vi.fn().mockResolvedValue(channelWindowResponse())
    vi.stubGlobal('fetch', fetchMock)

    const { unmount } = withMockedClientWidth(800, () =>
      render(EcgGraph, { props: { path: '/data/57.txt', channel: 'channel 1' } }),
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    const over = screen.getByTestId('ecg-graph-container').querySelector('.u-over') as HTMLDivElement
    expect(over).toBeTruthy()

    // Start a drag but never dispatch a natural mouseup — this is the
    // in-progress-drag state that leaves `document`-level listeners
    // attached until `onUp` fires (or, before the fix, forever).
    over.dispatchEvent(new MouseEvent('mousedown', { button: 0, clientX: 100, clientY: 100, bubbles: true }))

    unmount()

    const callsAfterUnmount = fetchMock.mock.calls.length

    // Simulate the drag "completing" via listeners left on `document`, as
    // would happen from any later unrelated mouseup on the page.
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 200, clientY: 100, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mouseup', { clientX: 200, clientY: 100, bubbles: true }))

    // No new fetch: the listeners were actually removed on unmount, not
    // just orphaned alongside a destroyed chart.
    expect(fetchMock.mock.calls.length).toBe(callsAfterUnmount)
  })
})
