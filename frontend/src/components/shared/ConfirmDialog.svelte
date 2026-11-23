<script lang="ts">
  import { onMount, type Snippet } from 'svelte'

  // A question before an action that changes or replaces saved review work:
  // what will happen, what is kept, and a clear way out. Cancel is the safe
  // default (Escape, a click on the backdrop, and the focused button).
  let {
    title,
    body,
    confirmLabel,
    cancelLabel = 'Cancel',
    danger = false,
    testid = 'confirm-dialog',
    onConfirm,
    onCancel,
  }: {
    title: string
    body: Snippet
    confirmLabel: string
    cancelLabel?: string
    // A red confirm button, for something that throws work away.
    danger?: boolean
    testid?: string
    onConfirm: () => void
    onCancel: () => void
  } = $props()

  const uid = $props.id()
  let cancelButton: HTMLButtonElement | undefined = $state()

  onMount(() => {
    cancelButton?.focus()
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        onCancel()
      }
    }
    document.addEventListener('keydown', handleKeydown, true)
    return () => document.removeEventListener('keydown', handleKeydown, true)
  })

  function handleBackdropClick(event: MouseEvent) {
    if (event.target === event.currentTarget) onCancel()
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="confirm-backdrop" data-testid={`${testid}-backdrop`} onclick={handleBackdropClick}>
  <div
    class="confirm-dialog"
    data-testid={testid}
    role="dialog"
    aria-modal="true"
    aria-labelledby={`${uid}-title`}
  >
    <h2 id={`${uid}-title`}>{title}</h2>
    <div class="confirm-body">{@render body()}</div>
    <div class="confirm-footer">
      <button type="button" class="btn" data-testid={`${testid}-cancel`} bind:this={cancelButton} onclick={onCancel}>
        {cancelLabel}
      </button>
      <button
        type="button"
        class="btn"
        class:btn-primary={!danger}
        class:btn-danger={danger}
        data-testid={`${testid}-confirm`}
        onclick={onConfirm}
      >
        {confirmLabel}
      </button>
    </div>
  </div>
</div>

<style>
  .confirm-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-4);
    z-index: 100;
  }

  .confirm-dialog {
    width: min(480px, 100%);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--color-surface-raised);
    color: var(--color-text);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
  }

  .confirm-dialog h2 {
    margin: 0;
    font-size: var(--font-size-lg);
  }

  .confirm-body {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    font-size: var(--font-size-sm);
    line-height: 1.5;
  }

  .confirm-body :global(p) {
    margin: 0;
  }

  .confirm-footer {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-2);
  }
</style>
