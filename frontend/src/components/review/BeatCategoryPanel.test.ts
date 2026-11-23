import { fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import BeatCategoryPanel from './BeatCategoryPanel.svelte'
import type { CategoryUpdateResult, WindowBeat } from '../../lib/api/types'

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as any).api
})

// Baseline fixture: no categories flagged. Individual tests override just the
// fields they care about, matching WindowBeat's exact field set (F3).
function makeBeat(overrides: Partial<WindowBeat> = {}): WindowBeat {
  return {
    ts: 12.5,
    rr: 0.8,
    r_amplitude: 6,
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

function mockCategoryResponse(body: Partial<CategoryUpdateResult>) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      status: 'ok',
      ts: null,
      review_state: null,
      reassigned_category: null,
      error: null,
      ...body,
    }),
  })
}

describe('BeatCategoryPanel', () => {
  it('renders only the categories that fired on this beat', () => {
    const beat = makeBeat({ tachycardia_absolute: true, bradycardia_absolute: false, skipped_beat: null })

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated: vi.fn() } })

    expect(screen.getByTestId('category-tachycardia_absolute')).toBeInTheDocument()
    expect(screen.queryByTestId('category-bradycardia_absolute')).not.toBeInTheDocument()
    expect(screen.queryByTestId('category-skipped_beat')).not.toBeInTheDocument()
  })

  it('flags a hand-added beat with the day and time it was added', () => {
    const beat = makeBeat({ manual_added_at: '2026-09-24T14:32:10+00:00' })

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated: vi.fn() } })

    const flag = screen.getByTestId('manual-beat-flag')
    expect(flag).toHaveTextContent('Added manually')
    expect(flag).toHaveTextContent('2026')
  })

  it('shows no manual flag on a detected beat', () => {
    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat: makeBeat(), onUpdated: vi.fn() } })

    expect(screen.queryByTestId('manual-beat-flag')).not.toBeInTheDocument()
  })

  it('shows a "no categories" message when nothing is flagged', () => {
    const beat = makeBeat() // every category false/null

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated: vi.fn() } })

    expect(screen.getByTestId('no-categories')).toBeInTheDocument()
    expect(screen.queryByTestId(/^category-/)).not.toBeInTheDocument()
  })

  it('calls updateBeatCategory with a confirm action and reports the result via onUpdated', async () => {
    const beat = makeBeat({ tachycardia_absolute: true })
    const fetchMock = mockCategoryResponse({ ts: beat.ts, review_state: 'confirmed' })
    vi.stubGlobal('fetch', fetchMock)
    const onUpdated = vi.fn()

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated } })
    await fireEvent.click(screen.getByTestId('confirm-button'))

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/files/beats/category',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ path: '/data/57.txt', ts: beat.ts, action: 'confirm', category: undefined }),
      }),
    )
    expect(onUpdated).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'ok', ts: beat.ts, review_state: 'confirmed' }),
    )
  })

  it('calls updateBeatCategory with a reject action', async () => {
    const beat = makeBeat({ skipped_beat: true })
    const fetchMock = mockCategoryResponse({ ts: beat.ts, review_state: 'rejected' })
    vi.stubGlobal('fetch', fetchMock)
    const onUpdated = vi.fn()

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated } })
    await fireEvent.click(screen.getByTestId('reject-button'))

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/files/beats/category',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ path: '/data/57.txt', ts: beat.ts, action: 'reject', category: undefined }),
      }),
    )
    expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({ status: 'ok', review_state: 'rejected' }))
  })

  it('offers exactly the 6 categories (not any_arrhythmia) as checkboxes, the current one ticked, and adds a ticked one', async () => {
    const beat = makeBeat({ prem_beat: true, any_arrhythmia: true })
    const fetchMock = mockCategoryResponse({
      ts: beat.ts,
      review_state: 'reviewed',
      reassigned_category: 'skipped_beat,prem_beat',
    })
    vi.stubGlobal('fetch', fetchMock)
    const onUpdated = vi.fn()

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated } })
    await fireEvent.click(screen.getByTestId('categories-picker-toggle'))

    const boxes = screen.getAllByRole('checkbox').filter((box) => box.dataset.testid?.startsWith('categories-option-'))
    expect(boxes.map((box) => box.dataset.testid?.replace('categories-option-', '')).sort()).toEqual(
      ['abn_cluster', 'bradycardia_absolute', 'other_arrhythmia', 'prem_beat', 'skipped_beat', 'tachycardia_absolute'].sort(),
    )
    expect(screen.getByTestId('categories-option-prem_beat')).toBeChecked()
    expect(screen.getByTestId('categories-option-skipped_beat')).not.toBeChecked()

    await fireEvent.click(screen.getByTestId('categories-option-skipped_beat'))

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/files/beats/category',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          path: '/data/57.txt',
          ts: beat.ts,
          action: 'set_categories',
          categories: ['skipped_beat', 'prem_beat'],
        }),
      }),
    )
    await waitFor(() => {
      expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({ status: 'ok' }))
    })
    expect(screen.getByTestId('category-skipped_beat')).toBeInTheDocument()
    expect(screen.getByTestId('category-prem_beat')).toBeInTheDocument()
  })

  it('removes an auto-assigned label via its × button, saving the remaining categories', async () => {
    const beat = makeBeat({ tachycardia_absolute: true, prem_beat: true })
    const fetchMock = mockCategoryResponse({ ts: beat.ts, review_state: 'unreviewed' })
    vi.stubGlobal('fetch', fetchMock)
    const onUpdated = vi.fn()

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated } })

    expect(screen.getByTestId('category-tachycardia_absolute')).toBeInTheDocument()
    expect(screen.getByTestId('category-prem_beat')).toBeInTheDocument()

    await fireEvent.click(screen.getByTestId('remove-category-tachycardia_absolute'))

    await waitFor(() => {
      expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({ status: 'ok' }))
    })
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/files/beats/category',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          path: '/data/57.txt',
          ts: beat.ts,
          action: 'set_categories',
          categories: ['prem_beat'],
        }),
      }),
    )

    // Removed immediately, without needing a re-fetch of `beat` — the other
    // flagged category is untouched.
    expect(screen.queryByTestId('category-tachycardia_absolute')).not.toBeInTheDocument()
    expect(screen.getByTestId('category-prem_beat')).toBeInTheDocument()
  })

  it('shows the last flagged category removed as "no categories flagged"', async () => {
    const beat = makeBeat({ tachycardia_absolute: true })
    vi.stubGlobal('fetch', mockCategoryResponse({ ts: beat.ts, review_state: 'unreviewed' }))

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated: vi.fn() } })
    await fireEvent.click(screen.getByTestId('remove-category-tachycardia_absolute'))

    expect(await screen.findByTestId('no-categories')).toBeInTheDocument()
  })

  it('asks for a category before marking a beat with none Arrhythmia, and saves it with the decision', async () => {
    const beat = makeBeat() // no category flagged
    const fetchMock = mockCategoryResponse({
      ts: beat.ts,
      review_state: 'confirmed',
      reassigned_category: 'other_arrhythmia',
    })
    vi.stubGlobal('fetch', fetchMock)

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated: vi.fn() } })

    // Arrhythmia alone saves nothing yet: the picker asks for a category.
    await fireEvent.click(screen.getByTestId('confirm-button'))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByTestId('categories-needed')).toBeInTheDocument()

    await fireEvent.click(screen.getByTestId('categories-option-other_arrhythmia'))

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/files/beats/category',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          path: '/data/57.txt',
          ts: beat.ts,
          action: 'confirm',
          categories: ['other_arrhythmia'],
        }),
      }),
    )
    await waitFor(() => {
      expect(screen.getByTestId('review-state')).toHaveTextContent('Arrhythmia')
    })
    expect(screen.getByTestId('category-other_arrhythmia')).toBeInTheDocument()
    expect(screen.queryByTestId('categories-needed')).not.toBeInTheDocument()
    // Its only category can't be unticked: an arrhythmia keeps one.
    expect(screen.getByTestId('categories-option-other_arrhythmia')).toBeDisabled()
  })

  it('shows a clean, retryable error state for the "no persisted data" backend error, distinct from a validation error', async () => {
    const beat = makeBeat({ tachycardia_absolute: true })
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'error',
        ts: null,
        review_state: null,
        reassigned_category: null,
        error: 'No persisted data for this file — run POST /files/beats first',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const onUpdated = vi.fn()

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated } })
    await fireEvent.click(screen.getByTestId('confirm-button'))

    expect(await screen.findByTestId('persist-required-error')).toHaveTextContent('No persisted data for this file')
    expect(screen.getByTestId('retry-category-action-button')).toBeInTheDocument()
    expect(screen.queryByTestId('validation-error')).not.toBeInTheDocument()
    expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({ status: 'error' }))
  })

  it('shows a plain validation error (distinct from the persist-required state) for an invalid category', async () => {
    const beat = makeBeat({ prem_beat: true })
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'error',
        ts: null,
        review_state: null,
        reassigned_category: null,
        error: "Invalid category: 'not_a_real_category'",
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(BeatCategoryPanel, { props: { path: '/data/57.txt', beat, onUpdated: vi.fn() } })
    await fireEvent.click(screen.getByTestId('confirm-button'))

    expect(await screen.findByTestId('validation-error')).toHaveTextContent('Invalid category')
    expect(screen.queryByTestId('persist-required-error')).not.toBeInTheDocument()
  })
})
