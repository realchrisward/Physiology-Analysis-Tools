import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ArrhythmiaControls from './ArrhythmiaControls.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

function arrhythmiaOkResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: async () => ({
      status: 'ok',
      beats: [],
      count: 10,
      any_arrhythmia_count: 2,
      elapsed_seconds: 0.5,
      error: null,
      ...overrides,
    }),
  }
}

function arrhythmiaErrorResponse(error: string) {
  return {
    ok: true,
    json: async () => ({
      status: 'error',
      beats: [],
      count: 0,
      any_arrhythmia_count: 0,
      elapsed_seconds: 0,
      error,
    }),
  }
}

describe('ArrhythmiaControls', () => {
  it.each([
    ['run-heuristic-button', 'heuristic'],
    ['run-unsupervised-button', 'unsupervised'],
    ['run-both-button', 'both'],
  ] as const)('calls detectArrhythmias with method %s', async (testId, method) => {
    const fetchMock = vi.fn().mockResolvedValue(arrhythmiaOkResponse())
    vi.stubGlobal('fetch', fetchMock)

    render(ArrhythmiaControls, { props: { path: '/data/57.txt', channel: 'channel 1', onComplete: vi.fn() } })
    await fireEvent.click(screen.getByTestId(testId))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://127.0.0.1:8000/arrhythmia/detect',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1', method }),
        }),
      )
    })
  })

  it('shows a running indicator and disables all three buttons while a run is in flight', async () => {
    let resolveFetch: ((value: unknown) => void) | undefined
    const pending = new Promise((resolve) => {
      resolveFetch = resolve
    })
    const fetchMock = vi.fn().mockReturnValue(pending)
    vi.stubGlobal('fetch', fetchMock)

    render(ArrhythmiaControls, { props: { path: '/data/57.txt', channel: 'channel 1', onComplete: vi.fn() } })

    expect(screen.queryByTestId('arrhythmia-running')).not.toBeInTheDocument()

    await fireEvent.click(screen.getByTestId('run-heuristic-button'))

    expect(screen.getByTestId('arrhythmia-running')).toBeInTheDocument()
    expect(screen.getByTestId('run-heuristic-button')).toBeDisabled()
    expect(screen.getByTestId('run-unsupervised-button')).toBeDisabled()
    expect(screen.getByTestId('run-both-button')).toBeDisabled()

    resolveFetch!(arrhythmiaOkResponse())
    await waitFor(() => expect(screen.queryByTestId('arrhythmia-running')).not.toBeInTheDocument())
  })

  it('calls onComplete and clears the running state on a successful run', async () => {
    const fetchMock = vi.fn().mockResolvedValue(arrhythmiaOkResponse())
    vi.stubGlobal('fetch', fetchMock)
    const onComplete = vi.fn()

    render(ArrhythmiaControls, { props: { path: '/data/57.txt', channel: 'channel 1', onComplete } })
    await fireEvent.click(screen.getByTestId('run-both-button'))

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('arrhythmia-running')).not.toBeInTheDocument()
    expect(screen.getByTestId('run-both-button')).not.toBeDisabled()
  })

  // Regression coverage for the exploit described alongside this fix: a
  // slow arrhythmia re-run started on one channel must not tell
  // ReviewWorkspace to refresh the graph after the technician has already
  // switched to a different channel — the backend's `beat_cache[path]` is
  // keyed only by path (see `backend/beats.py`), so a stale response landing
  // after the new channel's own `detectBeats` would otherwise clobber it
  // back to the old channel's (now wrong-channel) data. This component is
  // mounted as a live `channel` prop by ReviewWorkspace (not remounted via
  // `{#key activeChannel}`), so the guard lives here: `run` captures the
  // channel it was dispatched for and compares it against the CURRENT
  // `channel` prop once `detectArrhythmias` resolves.
  it('does not call onComplete when the channel prop changes while a run is still in flight', async () => {
    let resolveFetch: ((value: unknown) => void) | undefined
    const pending = new Promise((resolve) => {
      resolveFetch = resolve
    })
    const fetchMock = vi.fn().mockReturnValue(pending)
    vi.stubGlobal('fetch', fetchMock)
    const onComplete = vi.fn()

    const { rerender } = render(ArrhythmiaControls, {
      props: { path: '/data/57.txt', channel: 'channel 1', onComplete },
    })

    await fireEvent.click(screen.getByTestId('run-heuristic-button'))
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://127.0.0.1:8000/arrhythmia/detect',
        expect.objectContaining({
          body: JSON.stringify({ path: '/data/57.txt', channel: 'channel 1', method: 'heuristic' }),
        }),
      )
    })

    // Simulate ReviewWorkspace advancing `activeChannel` to channel 2 (e.g.
    // the technician switched and channel 2's own `detectBeats` resolved)
    // while channel 1's arrhythmia run is still pending. This component is a
    // live prop, not remounted, so its `channel` prop just updates in place.
    await rerender({ path: '/data/57.txt', channel: 'channel 2', onComplete })

    // Now let channel 1's stale response resolve.
    resolveFetch!(arrhythmiaOkResponse())
    await waitFor(() => expect(screen.queryByTestId('arrhythmia-running')).not.toBeInTheDocument())

    expect(onComplete).not.toHaveBeenCalled()
  })

  it('clears the running state, shows an error, and does not call onComplete on failure', async () => {
    const fetchMock = vi.fn().mockResolvedValue(arrhythmiaErrorResponse('boom'))
    vi.stubGlobal('fetch', fetchMock)
    const onComplete = vi.fn()

    render(ArrhythmiaControls, { props: { path: '/data/57.txt', channel: 'channel 1', onComplete } })
    await fireEvent.click(screen.getByTestId('run-heuristic-button'))

    await waitFor(() => {
      expect(screen.getByTestId('arrhythmia-error')).toHaveTextContent('boom')
    })
    expect(screen.queryByTestId('arrhythmia-running')).not.toBeInTheDocument()
    expect(onComplete).not.toHaveBeenCalled()
  })
})
