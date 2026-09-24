<script lang="ts">
  import { onMount } from 'svelte'
  import { formatRelativeTime } from '../../lib/format'
  import {
    handleImportFiles,
    handleImportFolder,
    handleStop,
    importQueue,
    openFiles,
    openRecentFile,
  } from '../../lib/stores/importQueue.svelte'
  import { recentFiles } from '../../lib/stores/recentFiles.svelte'
  import Icon from '../shared/Icon.svelte'

  // Keeps "Opened 5 minutes ago" honest while the screen stays open.
  let now: number = $state(Date.now())
  onMount(() => {
    const timer = setInterval(() => (now = Date.now()), 60_000)
    return () => clearInterval(timer)
  })

  // Files ticked for "Open selected". Kept as paths and always read through
  // `selectedPaths` so a file that has since dropped off the list can never
  // be opened by a stale tick.
  let ticked: string[] = $state([])
  let selectedPaths = $derived(recentFiles.map((f) => f.path).filter((p) => ticked.includes(p)))
  let allSelected = $derived(recentFiles.length > 0 && selectedPaths.length === recentFiles.length)

  function toggleTicked(path: string) {
    ticked = ticked.includes(path) ? ticked.filter((p) => p !== path) : [...ticked, path]
  }

  function toggleAll() {
    ticked = allSelected ? [] : recentFiles.map((f) => f.path)
  }

  function openAll() {
    void openFiles(recentFiles.map((f) => f.path))
  }

  function openSelected() {
    void openFiles(selectedPaths)
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
      <div class="recent-files-header">
        <h2 class="recent-files-title">Recent files</h2>
        <label class="select-all">
          <input
            type="checkbox"
            data-testid="select-all-recent-checkbox"
            checked={allSelected}
            onchange={toggleAll}
          />
          Select all
        </label>
        <span class="recent-files-spacer"></span>
        <button
          type="button"
          class="btn btn-sm"
          data-testid="open-selected-button"
          disabled={selectedPaths.length === 0 || importQueue.importing}
          onclick={openSelected}
        >
          Open selected{selectedPaths.length > 0 ? ` (${selectedPaths.length})` : ''}
        </button>
        <button
          type="button"
          class="btn btn-sm btn-primary"
          data-testid="open-all-button"
          disabled={importQueue.importing}
          onclick={openAll}
        >
          Open all ({recentFiles.length})
        </button>
      </div>
      <p class="recent-files-hint text-muted">
        Click a file to open just that one, or tick several and use Open selected. When more than one
        opens, the first is shown and the others are ready in the sidebar.
      </p>
      <div class="recent-files-grid">
        {#each recentFiles as file (file.path)}
          <div class="recent-file-item">
            <input
              type="checkbox"
              class="recent-file-checkbox"
              data-testid="recent-file-checkbox"
              aria-label={`Select ${file.filename}`}
              checked={ticked.includes(file.path)}
              onchange={() => toggleTicked(file.path)}
            />
            <button
              type="button"
              class="recent-file-card"
              data-testid="recent-file-card"
              onclick={() => openRecentFile(file.path)}
              disabled={importQueue.importing}
            >
              <Icon name="file-text" size={18} />
              <span class="recent-file-name">{file.filename}</span>
              <span class="recent-file-time text-muted" data-testid="recent-file-opened"
                >Opened {formatRelativeTime(file.openedAt, now)}</span
              >
            </button>
          </div>
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

  .recent-files-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2) var(--space-3);
  }

  .recent-files-spacer {
    flex: 1;
  }

  .recent-files-title {
    font-size: var(--font-size-sm);
    font-weight: 600;
    color: var(--color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .select-all {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
  }

  .recent-files-hint {
    margin: var(--space-2) 0 var(--space-3);
    font-size: 0.75rem;
  }

  .recent-file-item {
    position: relative;
    display: flex;
  }

  .recent-file-item .recent-file-card {
    flex: 1;
    min-width: 0;
  }

  /* Top-right corner of the card, clear of the file icon at top-left. */
  .recent-file-checkbox {
    position: absolute;
    top: var(--space-2);
    right: var(--space-2);
    z-index: 1;
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
