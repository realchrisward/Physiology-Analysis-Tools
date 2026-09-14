import { render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HealthStatus from './HealthStatus.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HealthStatus', () => {
  it('shows ok when the backend responds healthy', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: 'ok' }),
      }),
    )

    render(HealthStatus)

    await waitFor(() => {
      expect(screen.getByTestId('health-status')).toHaveTextContent('Backend: ok')
    })
  })

  it('shows error when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network error')))

    render(HealthStatus)

    await waitFor(() => {
      expect(screen.getByTestId('health-status')).toHaveTextContent('Backend: error')
    })
  })
})
