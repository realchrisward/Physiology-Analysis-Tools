<script lang="ts">
  import { handleStopImport, importQueue } from '../../lib/stores/importQueue.svelte'

  // "Importing 12 of 30 · M00753117.adicht" with a bar and Stop, while files
  // are being read (see importQueue's `progress`). Nothing while they aren't.
  // `compact` is the sidebar's narrow version.
  let { compact = false }: { compact?: boolean } = $props()

  let progress = $derived(importQueue.progress)
  let percent = $derived(progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0)
</script>

{#if progress}
  <div class="import-progress" class:compact data-testid="import-progress" role="status">
    <div class="row">
      <span class="spinner" aria-hidden="true"></span>
      <span class="label">
        Importing {Math.min(progress.done + 1, progress.total)} of {progress.total}
        {#if progress.current && !compact}<span class="current text-muted">· {progress.current}</span>{/if}
      </span>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="import-stop-button"
        title="Stop after the file being read now (files already imported stay)"
        onclick={handleStopImport}
      >
        Stop
      </button>
    </div>
    <div
      class="bar"
      role="progressbar"
      aria-label="Import progress"
      aria-valuemin="0"
      aria-valuemax={progress.total}
      aria-valuenow={progress.done}
    >
      <div class="fill" style:width={`${percent}%`}></div>
    </div>
    {#if progress.current && compact}
      <span class="current text-muted" title={progress.current}>{progress.current}</span>
    {/if}
  </div>
{/if}

<style>
  .import-progress {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    font-size: var(--font-size-sm);
  }

  .compact {
    gap: var(--space-1);
    padding: var(--space-2) var(--space-3);
    border: none;
    border-bottom: 1px solid var(--color-border);
    border-radius: 0;
    background: transparent;
  }

  .row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
  }

  .label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .current {
    font-size: 0.75rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .bar {
    height: 4px;
    border-radius: 999px;
    background: var(--color-surface-hover);
    overflow: hidden;
  }

  .fill {
    height: 100%;
    background: var(--color-accent);
    transition: width var(--transition-fast);
  }

  .spinner {
    display: inline-block;
    flex-shrink: 0;
    width: 12px;
    height: 12px;
    border: 2px solid var(--color-border);
    border-top-color: var(--color-accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
