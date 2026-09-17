<script lang="ts">
  import { updateBeatCategory } from '../../lib/api/persistence'
  import type { CategoryUpdateResult, WindowBeat } from '../../lib/api/types'
  import Icon from '../shared/Icon.svelte'

  // The 6 reassignable categories, exactly per `backend/categories.py`'s
  // `REASSIGNABLE_CATEGORIES` (documented in BACKEND_OVERVIEW.md).
  // `any_arrhythmia` is a derived summary flag, never a reassignment target
  // or a per-category badge — it is deliberately excluded from this list.
  const REASSIGNABLE_CATEGORIES = [
    'bradycardia_absolute',
    'tachycardia_absolute',
    'skipped_beat',
    'prem_beat',
    'abn_cluster',
    'other_arrhythmia',
  ] as const

  type ReassignableCategory = (typeof REASSIGNABLE_CATEGORIES)[number]

  const CATEGORY_LABELS: Record<ReassignableCategory, string> = {
    bradycardia_absolute: 'Bradycardia (absolute)',
    tachycardia_absolute: 'Tachycardia (absolute)',
    skipped_beat: 'Skipped beat',
    prem_beat: 'Premature beat',
    abn_cluster: 'Abnormal cluster',
    other_arrhythmia: 'Other arrhythmia',
  }

  let {
    path,
    beat,
    onUpdated,
  }: {
    path: string
    beat: WindowBeat
    onUpdated: (result: CategoryUpdateResult) => void
  } = $props()

  // `beat` is a plain prop from the parent's last fetch and never updates
  // on its own after a successful remove/reassign/reject call — so once
  // any action that changes WHICH categories are flagged succeeds, this
  // takes over as the source of truth for what's displayed, tracked
  // locally the same way `reviewState`/`reassignedCategory` already are,
  // rather than requiring the parent to re-fetch and re-pass a fresh
  // `beat`. `null` means "nothing's changed yet — derive from `beat` as
  // normal"; every action that touches flags (remove_flag, reassign,
  // reject — never confirm, which leaves flags untouched) sets it to the
  // exact new set the backend now has.
  let categoryOverride: ReassignableCategory[] | null = $state(null)

  // Only categories that actually fired (`=== true`) are ever shown as
  // auto-detected, matching the workflow spec exactly (see task brief step
  // 3) — unless overridden locally by a successful action (see above).
  let flaggedCategories = $derived(
    categoryOverride ?? REASSIGNABLE_CATEGORIES.filter((cat) => beat[cat] === true),
  )

  // Reflects the outcome of this beat's most recent successful action
  // locally, without re-fetching anything — `WindowBeat` itself carries no
  // `review_state`/`reassigned_category` fields (those only exist on the
  // persisted-beat shape), so this component tracks them itself from each
  // `CategoryUpdateResult` it receives.
  let reviewState: string | null = $state(null)
  let reassignedCategory: string | null = $state(null)

  let reassignCategory: string = $state('')
  let submitting: boolean = $state(false)

  // The error from the most recent failed action, and the action that
  // produced it (so "Retry" can replay the exact same call rather than
  // requiring the technician to re-pick a category first).
  let errorMessage: string | null = $state(null)
  let lastAttempt: { action: 'confirm' | 'reject' | 'reassign' | 'remove_flag'; category?: string } | null =
    $state(null)

  // The backend's specific "category isn't persisted yet" error (see
  // BACKEND_OVERVIEW.md / `backend/db_routes.py`) gets a distinct, retryable
  // presentation from an ordinary validation error (e.g. an invalid
  // category) — the former is a transient, recoverable precondition, the
  // latter means the request itself was wrong and retrying identically
  // would just fail again the same way.
  let isPersistRequiredError = $derived((errorMessage ?? '').includes('No persisted data for this file'))

  async function submit(action: 'confirm' | 'reject' | 'reassign' | 'remove_flag', category?: string) {
    submitting = true
    errorMessage = null
    lastAttempt = { action, category }

    const result = await updateBeatCategory(path, beat.ts, action, category)
    submitting = false

    if (result.status === 'ok' && !result.error) {
      reviewState = result.review_state
      reassignedCategory = result.reassigned_category

      // Mirrors exactly what `backend/db.py`'s `update_beat_category` just
      // did server-side, so the chip list updates immediately without a
      // re-fetch — see `categoryOverride`'s own comment above.
      if (action === 'remove_flag' && category) {
        categoryOverride = flaggedCategories.filter((cat) => cat !== category)
      } else if (action === 'reassign' && category) {
        categoryOverride = [category as ReassignableCategory]
      } else if (action === 'reject') {
        categoryOverride = []
      }
    } else {
      errorMessage = result.error ?? 'Category update failed'
    }

    onUpdated(result)
  }

  function retry() {
    if (lastAttempt === null) return
    void submit(lastAttempt.action, lastAttempt.category)
  }

  function removeFlag(cat: ReassignableCategory) {
    void submit('remove_flag', cat)
  }
</script>

<div class="beat-category-panel" data-testid="beat-category-panel">
  <p class="beat-summary" data-testid="selected-beat-summary">Selected beat: t={beat.ts}s, HR {beat.hr}, RR {beat.rr}</p>

  <div class="beat-categories" data-testid="beat-categories">
    {#if flaggedCategories.length === 0}
      <p class="text-muted" data-testid="no-categories">No arrhythmia categories flagged for this beat.</p>
    {:else}
      <ul class="category-list">
        {#each flaggedCategories as cat (cat)}
          <li class="category-chip" data-testid={`category-${cat}`}>
            {CATEGORY_LABELS[cat]}
            <button
              type="button"
              class="category-chip-remove"
              data-testid={`remove-category-${cat}`}
              disabled={submitting}
              title={`Remove "${CATEGORY_LABELS[cat]}"`}
              aria-label={`Remove ${CATEGORY_LABELS[cat]}`}
              onclick={() => removeFlag(cat)}
            >
              <Icon name="x" size={10} />
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  {#if reviewState}
    <p
      data-testid="review-state"
      class="review-state"
      class:review-state--confirmed={reviewState === 'confirmed'}
      class:review-state--rejected={reviewState === 'rejected'}
      class:review-state--reassigned={reviewState === 'reassigned'}
    >
      {#if reviewState === 'reassigned' && reassignedCategory}
        Reassigned to {CATEGORY_LABELS[reassignedCategory as ReassignableCategory] ?? reassignedCategory}
      {:else}
        Review state: {reviewState}
      {/if}
    </p>
  {/if}

  {#if errorMessage}
    {#if isPersistRequiredError}
      <div class="banner banner-error" data-testid="persist-required-error">
        <p>{errorMessage}</p>
        <button type="button" class="btn btn-sm" data-testid="retry-category-action-button" onclick={retry}>
          Retry
        </button>
      </div>
    {:else}
      <p class="text-danger" data-testid="validation-error">{errorMessage}</p>
    {/if}
  {/if}

  <div class="actions">
    <button
      type="button"
      class="btn btn-primary"
      data-testid="confirm-button"
      disabled={submitting}
      onclick={() => submit('confirm')}
    >
      Confirm
    </button>
    <button
      type="button"
      class="btn btn-danger-text"
      data-testid="reject-button"
      disabled={submitting}
      onclick={() => submit('reject')}
    >
      Reject
    </button>

    <label class="field">
      Reassign to:
      <select data-testid="reassign-select" bind:value={reassignCategory} disabled={submitting}>
        <option value="" disabled>Select category…</option>
        {#each REASSIGNABLE_CATEGORIES as cat}
          <option value={cat}>{CATEGORY_LABELS[cat]}</option>
        {/each}
      </select>
    </label>
    <button
      type="button"
      class="btn"
      data-testid="reassign-button"
      disabled={submitting || reassignCategory === ''}
      onclick={() => submit('reassign', reassignCategory)}
    >
      Reassign
    </button>
  </div>
</div>

<style>
  .beat-category-panel {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .beat-summary {
    font-size: var(--font-size-sm);
    font-weight: 500;
  }

  .category-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .category-chip {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    font-size: 0.75rem;
    font-weight: 500;
    padding: var(--space-1) var(--space-1) var(--space-1) var(--space-2);
    border-radius: 999px;
    background: var(--color-accent-soft, #eef1f5);
    color: var(--color-accent, #2563eb);
  }

  .category-chip-remove {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: none;
    color: inherit;
    opacity: 0.7;
  }

  .category-chip-remove:hover:not(:disabled) {
    opacity: 1;
    background: rgba(0, 0, 0, 0.1);
  }

  .category-chip-remove:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  /* Semantic status colors from tokens.css, distinguishing the three
     terminal review states at a glance during extended review sessions. */
  .review-state {
    font-weight: 600;
    font-size: var(--font-size-sm);
  }
  .review-state--confirmed {
    color: var(--color-success, #15803d);
  }
  .review-state--rejected {
    color: var(--color-danger, #b91c1c);
  }
  .review-state--reassigned {
    color: var(--color-warning, #b45309);
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: var(--space-2);
  }
</style>
