import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AboutDialog from './AboutDialog.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
})

function healthyBackend() {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) })
}

describe('AboutDialog', () => {
  it('reports the live backend connection rather than a hardcoded claim', async () => {
    vi.stubGlobal('fetch', healthyBackend())

    render(AboutDialog, { props: { onClose: vi.fn() } })

    await waitFor(() => {
      expect(screen.getByTestId('about-backend-status')).toHaveTextContent('connected')
    })
  })

  it('says so when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')))

    render(AboutDialog, { props: { onClose: vi.fn() } })

    await waitFor(() => {
      expect(screen.getByTestId('about-backend-status')).toHaveTextContent('not reachable')
    })
  })

  it('dismisses via the close button, the backdrop, and Escape', async () => {
    vi.stubGlobal('fetch', healthyBackend())

    const onClose = vi.fn()
    const { unmount } = render(AboutDialog, { props: { onClose } })

    await fireEvent.click(screen.getByTestId('about-close-button'))
    expect(onClose).toHaveBeenCalledTimes(1)

    await fireEvent.click(screen.getByTestId('about-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(2)

    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(3)

    // The Escape listener is document-level, so it must not outlive the
    // dialog.
    unmount()
    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(3)
  })

  it('does not dismiss on a click inside the dialog itself', async () => {
    vi.stubGlobal('fetch', healthyBackend())

    const onClose = vi.fn()
    render(AboutDialog, { props: { onClose } })

    await fireEvent.click(screen.getByTestId('about-dialog'))

    expect(onClose).not.toHaveBeenCalled()
  })
})
