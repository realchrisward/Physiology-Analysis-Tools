<script lang="ts">
  import { onMount } from 'svelte'
  import { getBackendUrl } from '../../lib/api'
  import Icon from '../shared/Icon.svelte'

  let { onClose }: { onClose: () => void } = $props()

  // Resolved from the live backend rather than hardcoded, so the version
  // shown is always the one actually answering requests.
  let backendUrl: string | null = $state(null)
  let backendReachable: boolean | null = $state(null)

  onMount(async () => {
    const url = await getBackendUrl()
    backendUrl = url
    try {
      const response = await fetch(`${url}/health`)
      backendReachable = response.ok
    } catch {
      backendReachable = false
    }
  })

  onMount(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeydown)
    return () => document.removeEventListener('keydown', handleKeydown)
  })

  function handleBackdropClick(event: MouseEvent) {
    if (event.target === event.currentTarget) onClose()
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="about-backdrop" data-testid="about-backdrop" onclick={handleBackdropClick}>
  <div class="about-dialog" data-testid="about-dialog" role="dialog" aria-modal="true" aria-label="About">
    <div class="about-header">
      <h2>About</h2>
      <button type="button" class="icon-btn" data-testid="close-about-button" aria-label="Close" onclick={onClose}>
        <Icon name="x" size={16} />
      </button>
    </div>

    <div class="about-body">
      <p class="about-title">Physiology Analysis Tools</p>
      <p class="text-muted">
        ECG analysis for animal research recordings: beat detection, arrhythmia
        detection, human review, and Excel export.
      </p>

      <dl class="about-facts">
        <dt>Analysis backend</dt>
        <dd data-testid="about-backend-status">
          {#if backendReachable === null}
            checking…
          {:else if backendReachable}
            connected{backendUrl ? ` (${backendUrl})` : ''}
          {:else}
            not reachable{backendUrl ? ` (${backendUrl})` : ''}
          {/if}
        </dd>
        <dt>Licence</dt>
        <dd>GPLv3</dd>
      </dl>
    </div>

    <div class="about-footer">
      <button type="button" class="btn btn-primary" data-testid="about-close-button" onclick={onClose}>Close</button>
    </div>
  </div>
</div>

<style>
  .about-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    padding: var(--space-4);
  }

  .about-dialog {
    background: var(--color-surface);
    border-radius: var(--radius-md, 8px);
    box-shadow: var(--shadow-lg);
    width: min(440px, 100%);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
  }

  .about-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .about-header h2 {
    margin: 0;
    font-size: var(--font-size-lg);
  }

  .about-body {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .about-title {
    margin: 0;
    font-weight: 600;
  }

  .about-facts {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: var(--space-1) var(--space-3);
    margin: var(--space-2) 0 0;
    font-size: var(--font-size-sm);
  }

  .about-facts dt {
    font-weight: 600;
    color: var(--color-text-muted);
  }

  .about-facts dd {
    margin: 0;
    word-break: break-all;
  }

  .about-footer {
    display: flex;
    justify-content: flex-end;
  }
</style>
