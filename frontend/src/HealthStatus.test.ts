import { render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HealthStatus from './HealthStatus.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HealthStatus', () => {
  it('shows ok when the backend responds healthy', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(HealthStatus)

    await waitFor(() => {
      expect(screen.getByTestId('health-status')).toHaveTextContent('Backend: ok')
    })
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/health')
  })

  it('shows error when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network error')))

    render(HealthStatus)

    await waitFor(() => {
      expect(screen.getByTestId('health-status')).toHaveTextContent('Backend: error')
    })
  })

  it('shows error when the backend returns a non-ok HTTP status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))

    render(HealthStatus)

    await waitFor(() => {
      expect(screen.getByTestId('health-status')).toHaveTextContent('Backend: error')
    })
  })

  it('shows error when the backend body reports a non-ok status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: 'degraded' }),
      }),
    )

    render(HealthStatus)

    await waitFor(() => {
      expect(screen.getByTestId('health-status')).toHaveTextContent('Backend: error')
    })
  })

  it('uses the port from window.api when running inside Electron', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) }),
    )
    ;(window as any).api = { getBackendPort: vi.fn().mockResolvedValue(9999) }

    render(HealthStatus)

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith('http://127.0.0.1:9999/health')
    })

    delete (window as any).api
  })
})
