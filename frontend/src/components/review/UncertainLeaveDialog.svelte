<script lang="ts">
  import { onMount } from 'svelte'

  // Shown when the reviewer ticked "Uncertain" on a beat and tries to move on
  // without confirming or rejecting it (see lib/stores/reviewGuard.svelte.ts).
  //   onStay  - go back to the beat (also Escape and a click on the backdrop).
  //   onLeave - move on anyway; the uncertain mark is discarded.
  let { onStay, onLeave }: { onStay: () => void; onLeave: () => void } = $props()

  let stayButton: HTMLButtonElement | undefined = $state()

  onMount(() => {
    // The safe choice is the default one, so Enter keeps the reviewer on the beat.
    stayButton?.focus()

    function handleKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') onStay()
    }
    document.addEventListener('keydown', handleKeydown)
    return () => document.removeEventListener('keydown', handleKeydown)
  })

  function handleBackdropClick(event: MouseEvent) {
    if (event.target === event.currentTarget) onStay()
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- Escape is handled globally via the onMount keydown listener above; this
     backdrop only needs to catch a direct click on itself to dismiss, so it is
     deliberately not a focusable control. -->
<div class="uncertain-backdrop" data-testid="uncertain-leave-backdrop" onclick={handleBackdropClick}>
  <div
    class="uncertain-dialog"
    data-testid="uncertain-leave-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="uncertain-leave-title"
  >
    <h2 id="uncertain-leave-title">Confirm or reject this beat first</h2>
    <p>
      You marked this beat as uncertain. Uncertain beats still need your best call — press Confirm (C) or Reject (R).
      The uncertain mark is saved together with that decision so these beats can be weighted or re-checked when the
      training data is built.
    </p>
    <div class="uncertain-footer">
      <button type="button" class="btn" data-testid="uncertain-leave-confirm" onclick={onLeave}>
        Leave without the mark
      </button>
      <button
        type="button"
        class="btn btn-primary"
        data-testid="uncertain-leave-cancel"
        bind:this={stayButton}
        onclick={onStay}
      >
        Go back to the beat
      </button>
    </div>
  </div>
</div>

<style>
  .uncertain-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-4);
    z-index: 100;
  }

  .uncertain-dialog {
    width: min(460px, 100%);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--color-surface-raised);
    color: var(--color-text);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
  }

  .uncertain-dialog h2 {
    margin: 0;
    font-size: var(--font-size-lg);
  }

  .uncertain-dialog p {
    margin: 0;
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
    line-height: 1.5;
  }

  .uncertain-footer {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-2);
  }
</style>
