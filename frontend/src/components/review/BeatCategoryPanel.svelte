<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import { deleteBeat, updateBeatCategory, type BeatReviewAction } from '../../lib/api/persistence'
  import { clearPendingUncertain, isPendingUncertain, setPendingUncertain } from '../../lib/stores/reviewGuard.svelte'
  import { shouldIgnoreShortcut } from '../../lib/shortcuts'
  import type { CategoryUpdateResult, WindowBeat } from '../../lib/api/types'
  import Icon from '../shared/Icon.svelte'
  import { formatEditTime } from '../../lib/format'

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

  const UNCERTAIN_TITLE =
    'Tick this if you are not sure of your call. It helps grade the quality of the training data. The beat still needs Arrhythmia or Not arrhythmia — the mark is saved together with that decision. (U)'

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
    onDeleted,
    onTouched,
  }: {
    path: string
    beat: WindowBeat
    onUpdated: (result: CategoryUpdateResult) => void
    // Fired after a beat is deleted outright (a detection false positive),
    // so the parent can refresh the graph and drop the selection.
    onDeleted?: () => void
    // Something was done to the beat that saves nothing yet (Unsure ticked
    // before deciding): the parent counts the beat as looked at.
    onTouched?: () => void
  } = $props()

  // `beat` is a plain prop from the parent's last fetch and never updates
  // on its own after a successful remove/reassign call — so once
  // any action that changes WHICH categories are flagged succeeds, this
  // takes over as the source of truth for what's displayed, tracked
  // locally the same way `reviewState`/`reassignedCategory` already are,
  // rather than requiring the parent to re-fetch and re-pass a fresh
  // `beat`. `null` means "nothing's changed yet — derive from `beat` as
  // normal"; every action that touches flags (remove_flag, reassign — never
  // confirm or reject, which leave flags untouched) sets it to the exact new
  // set the backend now has.
  let categoryOverride: ReassignableCategory[] | null = $state(null)

  // Only categories that actually fired (`=== true`) are ever shown as
  // auto-detected, matching the workflow spec exactly (see task brief step
  // 3) — unless overridden locally by a successful action (see above).
  let detectedCategories = $derived(
    categoryOverride ?? REASSIGNABLE_CATEGORIES.filter((cat) => beat[cat] === true),
  )

  // Seeded from the beat itself (so stepping onto a beat judged earlier —
  // this session or a prior one — shows that judgment immediately, not a
  // blank unreviewed state), then kept current locally from each
  // `CategoryUpdateResult` this component's own actions receive, without
  // re-fetching anything, and from the parent's own changes to `beat` (see
  // the effect below). This component is remounted per beat (see the
  // `{#key selectedBeat.ts}` wrapper in ReviewWorkspace).
  let reviewState: string | null = $state(
    beat.review_state && beat.review_state !== 'unreviewed' ? beat.review_state : null,
  )
  let reassignedCategory: string | null = $state(beat.reassigned_category ?? null)

  // The parent marks a beat reviewed as soon as it is selected, and keeps
  // `beat` current with every saved action: follow its review state.
  $effect.pre(() => {
    const state = beat.review_state
    const category = beat.reassigned_category ?? null
    untrack(() => {
      reviewState = state && state !== 'unreviewed' ? state : null
      reassignedCategory = category
    })
  })

  // A rejected beat keeps the detector's flags (for the report), but none of
  // them stands: it lists no categories. Arrhythmia brings them back.
  let flaggedCategories = $derived(reviewState === 'rejected' ? [] : detectedCategories)

  // Review states: null (not looked at), 'reviewed' (looked at, no
  // decision), 'confirmed' (Arrhythmia) or 'rejected' (Not arrhythmia).
  // `reassignedCategory` can also be set from the one existing test
  // fixture that feeds this component a synthetic `review_state:
  // 'reassigned'` the backend itself never actually sends (reassign really
  // returns `review_state: 'confirmed'` — see backend/db.py) — treated the
  // same way `reviewState === 'rejected'` only ever excludes it, not the
  // reverse, so a rejected beat is never mislabeled as reassigned.
  let isReassigned = $derived(!!reassignedCategory && reviewState !== 'rejected')
  let hasStatus = $derived(reviewState === 'confirmed' || reviewState === 'rejected')

  // "Uncertain / not sure" is only ever saved together with a decision. Once the
  // beat has one, `savedUncertain` is the saved mark and the box saves a change
  // at once; before that, ticking it only leaves a PENDING mark in the review
  // guard (a store, since this panel is remounted per beat and moves between
  // the side panel and the focus overlay), sent with the next Confirm / Reject /
  // Reassign. The box shows whichever applies.
  type CategoryAction = BeatReviewAction
  let savedUncertain: boolean = $state(beat.uncertain === true)
  let pendingUncertain = $derived(!hasStatus && isPendingUncertain(path, beat.ts))
  let uncertain = $derived(hasStatus ? savedUncertain : pendingUncertain)

  let reassignCategory: string = $state('')
  let submitting: boolean = $state(false)

  // Arrhythmia and Not arrhythmia work as a pair: pressing the other one
  // changes the decision, pressing the chosen one again takes it off (the
  // beat stays reviewed). Not while Unsure is ticked: an unsure beat keeps a
  // decision, so the chosen one stays put until Unsure is unticked.
  let decisionLocked = $derived(hasStatus && savedUncertain)

  function isChosen(action: 'confirm' | 'reject'): boolean {
    return action === 'confirm' ? reviewState === 'confirmed' : reviewState === 'rejected'
  }

  function decide(action: 'confirm' | 'reject') {
    if (!isChosen(action)) void submit(action)
    else if (!decisionLocked) void submit('clear_decision')
  }

  function decisionTitle(action: 'confirm' | 'reject'): string {
    if (isChosen(action)) {
      return decisionLocked
        ? 'Untick Unsure to take this decision off: an unsure beat keeps its decision'
        : 'Click again to take the decision off (the beat stays reviewed)'
    }
    return action === 'confirm' ? 'This beat is an arrhythmia (C)' : 'This beat is not an arrhythmia (R)'
  }

  // The error from the most recent failed action, and the action that
  // produced it (so "Retry" can replay the exact same call rather than
  // requiring the technician to re-pick a category first).
  let errorMessage: string | null = $state(null)
  let lastAttempt: { action: CategoryAction; category?: string; uncertain?: boolean } | null = $state(null)

  // The backend's specific "category isn't persisted yet" error (see
  // BACKEND_OVERVIEW.md / `backend/db_routes.py`) gets a distinct, retryable
  // presentation from an ordinary validation error (e.g. an invalid
  // category) — the former is a transient, recoverable precondition, the
  // latter means the request itself was wrong and retrying identically
  // would just fail again the same way.
  let isPersistRequiredError = $derived((errorMessage ?? '').includes('No persisted data for this file'))

  async function submit(action: CategoryAction, category?: string, newMark?: boolean) {
    const decides = action === 'confirm' || action === 'reject' || action === 'reassign'
    // A mark ticked before deciding goes with the decision; with none pending
    // the beat's current mark is left as it is. `newMark` is the value a
    // `set_uncertain` call saves.
    const sentUncertain = action === 'set_uncertain' ? newMark : decides && pendingUncertain ? true : undefined

    submitting = true
    errorMessage = null
    lastAttempt = { action, category, uncertain: newMark }

    const result = await updateBeatCategory(path, beat.ts, action, category, sentUncertain)
    submitting = false

    if (result.status === 'ok' && !result.error) {
      // Changing only the mark leaves the decision as it is.
      if (action !== 'set_uncertain') {
        reviewState = result.review_state
        reassignedCategory = result.reassigned_category
      }
      savedUncertain = result.uncertain ?? sentUncertain ?? savedUncertain
      if (decides) clearPendingUncertain(path, beat.ts)

      // Mirrors exactly what `backend/db.py`'s `update_beat_category` just
      // did server-side, so the chip list updates immediately without a
      // re-fetch — see `categoryOverride`'s own comment above.
      if (action === 'remove_flag' && category) {
        categoryOverride = detectedCategories.filter((cat) => cat !== category)
      } else if (action === 'reassign' && category) {
        categoryOverride = [category as ReassignableCategory]
      }
    } else {
      errorMessage = result.error ?? 'Category update failed'
    }

    onUpdated(result)
  }

  // Deleting a beat is different from rejecting it: reject says "this beat
  // is real but isn't an arrhythmia", delete says "this isn't a beat at
  // all" and removes it from the counts, the graph, and the export. Asks
  // first, since unlike every other action here it cannot be undone.
  let confirmingDelete: boolean = $state(false)

  async function confirmDelete() {
    submitting = true
    errorMessage = null
    const result = await deleteBeat(path, beat.ts)
    submitting = false
    confirmingDelete = false

    if (result.status === 'ok' && !result.error) {
      // The beat is gone, and so is anything pending on it.
      clearPendingUncertain(path, beat.ts)
      onDeleted?.()
    } else {
      errorMessage = result.error ?? 'Could not delete this beat'
    }
  }

  // Ticks or unticks "Uncertain". On a beat that already has a decision that
  // saves at once; otherwise it only (un)sets the pending mark.
  async function setUncertain(next: boolean) {
    if (submitting || next === uncertain) return
    if (hasStatus) await submit('set_uncertain', undefined, next)
    else if (next) {
      setPendingUncertain(path, beat.ts)
      onTouched?.()
    }
    else clearPendingUncertain(path, beat.ts)
  }

  // A mouse click leaves the checkbox focused, and a focused <input> makes the
  // shared shortcut guard drop C / R / U (see `shouldIgnoreShortcut`): hand
  // focus back, as EcgGraph does for its report filter. A keyboard toggle
  // (Space) keeps it.
  let boxPointer = false

  async function handleUncertainChange(event: Event) {
    const box = event.currentTarget as HTMLInputElement
    if (boxPointer) box.blur()
    boxPointer = false
    await setUncertain(box.checked)
    // A failed save leaves the saved mark as it was: show that, not the click.
    box.checked = uncertain
  }

  // Confirm/reject/uncertain from the keyboard, so the common case (walk to a
  // flagged beat, judge it, move on) never needs the mouse. Mounted per
  // selected beat, so the shortcut always acts on the beat currently shown.
  onMount(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (shouldIgnoreShortcut(event) || submitting) return
      if (event.key === 'c' || event.key === 'C') {
        event.preventDefault()
        void submit('confirm')
      } else if (event.key === 'r' || event.key === 'R') {
        event.preventDefault()
        void submit('reject')
      } else if (event.key === 'u' || event.key === 'U') {
        event.preventDefault()
        void setUncertain(!uncertain)
      }
    }
    document.addEventListener('keydown', handleKeydown)
    return () => document.removeEventListener('keydown', handleKeydown)
  })

  function retry() {
    if (lastAttempt === null) return
    void submit(lastAttempt.action, lastAttempt.category, lastAttempt.uncertain)
  }

  function removeFlag(cat: ReassignableCategory) {
    void submit('remove_flag', cat)
  }
</script>

<div class="beat-category-panel" data-testid="beat-category-panel">
  <p class="beat-summary" data-testid="selected-beat-summary">Selected beat: t={beat.ts}s, HR {beat.hr}, RR {beat.rr}</p>
  {#if beat.manual_added_at}
    <p class="manual-flag" data-testid="manual-beat-flag">
      <Icon name="plus" size={12} />
      Added manually · {formatEditTime(beat.manual_added_at)}
    </p>
  {/if}

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

  <div class="judged-status" data-testid="review-state-row">
    <p
      data-testid="review-state"
      class="review-state"
      class:review-state--confirmed={reviewState === 'confirmed' && !isReassigned}
      class:review-state--rejected={reviewState === 'rejected'}
      class:review-state--reassigned={isReassigned}
      class:review-state--open={!hasStatus && !isReassigned}
    >
      {#if isReassigned}
        Reassigned to {CATEGORY_LABELS[reassignedCategory as ReassignableCategory] ?? reassignedCategory}
      {:else if reviewState === 'confirmed'}
        Arrhythmia
      {:else if reviewState === 'rejected'}
        Not arrhythmia
      {:else if reviewState}
        Reviewed · no decision
      {:else}
        Not reviewed yet
      {/if}
      {#if hasStatus && savedUncertain}
        <span data-testid="uncertain-state"> · uncertain</span>
      {/if}
    </p>
    {#if reviewState}
      <button
        type="button"
        class="btn btn-sm btn-ghost"
        data-testid="mark-unreviewed-button"
        disabled={submitting || decisionLocked}
        title={decisionLocked
          ? 'Untick Unsure first: an unsure beat keeps its decision'
          : 'Mark this beat as not reviewed yet (takes its decision off)'}
        onclick={() => submit('unreview')}
      >
        Mark as unreviewed
      </button>
    {/if}
  </div>

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

  <div class="uncertain-row">
    <label class="uncertain-toggle" title={UNCERTAIN_TITLE} onpointerdown={() => (boxPointer = true)}>
      <input
        type="checkbox"
        data-testid="uncertain-checkbox"
        title={UNCERTAIN_TITLE}
        checked={uncertain}
        disabled={submitting}
        onchange={handleUncertainChange}
        onkeydown={() => (boxPointer = false)}
      />
      Uncertain / not sure <kbd>U</kbd>
    </label>
    {#if pendingUncertain}
      <p class="uncertain-hint" data-testid="uncertain-pending-hint">
        Now choose Arrhythmia or Not arrhythmia — the mark is saved with your decision.
      </p>
    {/if}
  </div>

  <div class="actions">
    <div class="decision" role="group" aria-label="Decision">
      <button
        type="button"
        class="btn decision-btn decision-btn--arrhythmia"
        class:decision-btn--on={isChosen('confirm')}
        aria-pressed={isChosen('confirm')}
        data-testid="confirm-button"
        disabled={submitting || (isChosen('confirm') && decisionLocked)}
        onclick={() => decide('confirm')}
        title={decisionTitle('confirm')}
      >
        Arrhythmia <kbd>C</kbd>
      </button>
      <button
        type="button"
        class="btn decision-btn decision-btn--not"
        class:decision-btn--on={isChosen('reject')}
        aria-pressed={isChosen('reject')}
        data-testid="reject-button"
        disabled={submitting || (isChosen('reject') && decisionLocked)}
        onclick={() => decide('reject')}
        title={decisionTitle('reject')}
      >
        Not arrhythmia <kbd>R</kbd>
      </button>
    </div>

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

  <div class="danger-zone">
    {#if confirmingDelete}
      <span class="text-danger" data-testid="delete-beat-confirm-prompt">
        Delete this beat entirely? It will leave the graph, the counts, and the report.
      </span>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="cancel-delete-beat-button"
        disabled={submitting}
        onclick={() => (confirmingDelete = false)}
      >
        Cancel
      </button>
      <button
        type="button"
        class="btn btn-sm btn-danger-text"
        data-testid="confirm-delete-beat-button"
        disabled={submitting}
        onclick={confirmDelete}
      >
        Delete beat
      </button>
    {:else}
      <button
        type="button"
        class="btn btn-sm btn-danger-text"
        data-testid="delete-beat-button"
        title="Not a real beat? Remove it from the data entirely"
        disabled={submitting}
        onclick={() => (confirmingDelete = true)}
      >
        <Icon name="trash" size={12} /> Not a beat
      </button>
    {/if}
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

  .manual-flag {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    align-self: flex-start;
    margin: 0;
    padding: 2px var(--space-2);
    border-radius: var(--radius-sm, 4px);
    background: var(--color-accent-soft);
    color: var(--color-accent);
    font-size: 0.75rem;
    font-weight: 600;
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

  .judged-status {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
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
  .review-state--open {
    color: var(--color-text-muted);
  }

  .decision {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .decision-btn--arrhythmia.decision-btn--on {
    background: var(--color-accent);
    border-color: var(--color-accent);
    color: #ffffff;
  }

  .decision-btn--not {
    color: var(--color-danger, #b91c1c);
  }

  .decision-btn--not.decision-btn--on {
    background: var(--color-danger, #b91c1c);
    border-color: var(--color-danger, #b91c1c);
    color: #ffffff;
  }

  .decision-btn--on kbd {
    color: inherit;
    border-color: currentColor;
    background: transparent;
  }

  .uncertain-row {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .uncertain-toggle {
    display: inline-flex;
    align-items: center;
    align-self: flex-start;
    gap: var(--space-2);
    font-size: var(--font-size-sm);
    cursor: pointer;
  }

  .uncertain-toggle input {
    accent-color: var(--color-accent);
  }

  .uncertain-hint {
    margin: 0;
    font-size: 0.75rem;
    color: var(--color-warning, #b45309);
  }

  .danger-zone {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
    padding-top: var(--space-2);
    border-top: 1px solid var(--color-border, #d3dae1);
    font-size: var(--font-size-sm);
  }

  .danger-zone .text-danger {
    flex: 1;
  }

  kbd {
    margin-left: 4px;
    padding: 0 4px;
    border: 1px solid currentColor;
    border-radius: 3px;
    font-size: 0.65rem;
    font-family: inherit;
    opacity: 0.6;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: var(--space-2);
  }
</style>
