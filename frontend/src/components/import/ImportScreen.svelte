<script lang="ts">
  import { handleImportFiles, handleImportFolder, handleStop, importQueue, reimportPath } from '../../lib/stores/importQueue.svelte'
  import { recentFiles } from '../../lib/stores/recentFiles.svelte'
  import Icon from '../shared/Icon.svelte'

  function formatRelativeTime(ts: number): string {
    const diffMs = Date.now() - ts
    const minutes = Math.round(diffMs / 60000)
    if (minutes < 1) return 'just now'
    if (minutes < 60) return `${minutes}m ago`
    const hours = Math.round(minutes / 60)
    if (hours < 24) return `${hours}h ago`
    const days = Math.round(hours / 24)
    if (days <= 1) return 'Yesterday'
    return `${days}d ago`
  }
</script>

<div class="welcome">
  <div class="welcome-hero">
    <div class="welcome-mark"><Icon name="activity" size={32} /></div>
    <h1 class="welcome-title">Welcome to Physiology Analysis Tools</h1>
    <p class="welcome-subtitle">Import ECG recordings to detect beats and review arrhythmias.</p>

    <div class="welcome-controls">
      <button
        type="button"
        class="btn btn-primary"
        data-testid="import-files-button"
        onclick={handleImportFiles}
        disabled={importQueue.importing}
      >
        <Icon name="file-plus" size={16} /> Import Files
      </button>
      <button
        type="button"
        class="btn"
        data-testid="import-folder-button"
        onclick={handleImportFolder}
        disabled={importQueue.importing}
      >
        <Icon name="folder" size={16} /> Import Folder
      </button>
      <label class="field field-inline">
        <input
          type="checkbox"
          data-testid="auto-run-checkbox"
          bind:checked={importQueue.autoRun}
          disabled={importQueue.importing}
        />
        Auto-run beat detection
      </label>
      {#if importQueue.detecting}
        <button type="button" class="btn btn-ghost btn-danger-text" data-testid="stop-button" onclick={handleStop}>
          Stop
        </button>
      {/if}
    </div>

    {#if importQueue.error}
      <p class="banner banner-error" data-testid="import-error">Import failed: {importQueue.error}</p>
    {/if}
  </div>

  {#if recentFiles.length > 0}
    <div class="recent-files">
      <h2 class="recent-files-title">Recent files</h2>
      <div class="recent-files-grid">
        {#each recentFiles as file (file.path)}
          <button
            type="button"
            class="recent-file-card"
            data-testid="recent-file-card"
            onclick={() => reimportPath(file.path)}
            disabled={importQueue.importing}
          >
            <Icon name="file-text" size={18} />
            <span class="recent-file-name">{file.filename}</span>
            <span class="recent-file-time text-muted">{formatRelativeTime(file.openedAt)}</span>
          </button>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .welcome {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: var(--space-6) var(--space-4);
    max-width: 720px;
    margin: 0 auto;
  }

  .welcome-hero {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: var(--space-3);
    padding-top: var(--space-6);
  }

  .welcome-mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 64px;
    height: 64px;
    border-radius: var(--radius-lg);
    background: var(--color-accent-soft);
    color: var(--color-accent);
    margin-bottom: var(--space-2);
  }

  .welcome-title {
    font-size: var(--font-size-xl);
    font-weight: 600;
    color: var(--color-text);
  }

  .welcome-subtitle {
    color: var(--color-text-muted);
    max-width: 440px;
  }

  .welcome-controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    margin-top: var(--space-3);
  }

  .banner-error {
    margin-top: var(--space-4);
    max-width: 480px;
  }

  .recent-files {
    width: 100%;
    margin-top: var(--space-6);
  }

  .recent-files-title {
    font-size: var(--font-size-sm);
    font-weight: 600;
    color: var(--color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: var(--space-3);
  }

  .recent-files-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: var(--space-3);
  }

  .recent-file-card {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-2);
    padding: var(--space-3);
    border-radius: var(--radius-md);
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    color: var(--color-text);
    text-align: left;
    transition: box-shadow var(--transition-fast), border-color var(--transition-fast);
  }

  .recent-file-card:hover:not(:disabled) {
    box-shadow: var(--shadow-sm);
    border-color: var(--color-accent);
  }

  .recent-file-name {
    font-size: var(--font-size-sm);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .recent-file-time {
    font-size: 0.75rem;
  }
</style>
