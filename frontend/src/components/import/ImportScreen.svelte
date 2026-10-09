<script lang="ts">
  import { onMount } from 'svelte'
  import { formatRelativeTime } from '../../lib/format'
  import { clearFlagProjects, flagProjects, startFlagListImport } from '../../lib/stores/flagProjects.svelte'
  import {
    handleImportFiles,
    handleImportFolder,
    handleStop,
    importQueue,
    openFiles,
    openRecentFile,
  } from '../../lib/stores/importQueue.svelte'
  import { clearRecentFiles, recentFiles, removeRecentFiles } from '../../lib/stores/recentFiles.svelte'
  import FlagProjectList from '../flags/FlagProjectList.svelte'
  import Icon from '../shared/Icon.svelte'

  // Keeps "Opened 5 minutes ago" honest while the screen stays open.
  let now: number = $state(Date.now())
  onMount(() => {
    const timer = setInterval(() => (now = Date.now()), 60_000)
    return () => clearInterval(timer)
  })

  // Whether each collapsible section is expanded, remembered across restarts.
  // Open by default; a blocked/unavailable localStorage just means the choice
  // isn't remembered.
  const FLAG_PROJECTS_OPEN_KEY = 'pat.home.flagProjectsOpen'
  const RECENT_FILES_OPEN_KEY = 'pat.home.recentFilesOpen'

  function loadOpen(key: string): boolean {
    try {
      return localStorage.getItem(key) !== 'false'
    } catch {
      return true
    }
  }

  function saveOpen(key: string, open: boolean) {
    try {
      localStorage.setItem(key, String(open))
    } catch {
      // ignore
    }
  }

  let flagProjectsOpen: boolean = $state(loadOpen(FLAG_PROJECTS_OPEN_KEY))
  let recentFilesOpen: boolean = $state(loadOpen(RECENT_FILES_OPEN_KEY))

  function toggleFlagProjects() {
    flagProjectsOpen = !flagProjectsOpen
    saveOpen(FLAG_PROJECTS_OPEN_KEY, flagProjectsOpen)
  }

  function toggleRecentFiles() {
    recentFilesOpen = !recentFilesOpen
    saveOpen(RECENT_FILES_OPEN_KEY, recentFilesOpen)
  }

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

  // Clearing drops entries from the Home screen's own recent-files list only
  // — it never touches the raw files on disk, nor the Sidebar's separate,
  // already-imported file list (`fileRegistry`'s own "Clear" button covers
  // that). Ticks referencing removed paths fall away automatically via
  // `selectedPaths`'s own derivation, but `ticked` is reset too so a stale
  // path can't linger and silently re-select a later, unrelated file.
  function clearSelected() {
    removeRecentFiles(selectedPaths)
    ticked = []
  }

  function clearAll() {
    clearRecentFiles()
    ticked = []
  }
</script>

<div class="welcome">
  <div class="welcome-hero">
    <div class="welcome-mark"><Icon name="activity" size={28} /></div>
    <h1 class="welcome-title">Welcome to Physiology Analysis Tools</h1>
    <p class="welcome-subtitle">Import ECG recordings to detect beats and review arrhythmias.</p>
  </div>

  <div class="action-cards">
    <section class="action-card" aria-labelledby="ecg-card-title">
      <div class="action-card-head">
        <span class="action-card-icon"><Icon name="file-text" size={20} /></span>
        <div class="action-card-text">
          <h2 class="action-card-title" id="ecg-card-title">ECG recordings</h2>
          <p class="action-card-desc text-muted">Import recordings to detect beats and review arrhythmias.</p>
        </div>
      </div>
      <div class="action-card-actions">
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
    </section>

    <section class="action-card" aria-labelledby="flag-card-title">
      <div class="action-card-head">
        <span class="action-card-icon"><Icon name="target" size={20} /></span>
        <div class="action-card-text">
          <h2 class="action-card-title" id="flag-card-title">False positive / negative review</h2>
          <p class="action-card-desc text-muted">
            Load a model's FP/FN list (a CSV of animal IDs and beat timestamps). Matching recordings open zoomed
            to each flagged beat.
          </p>
        </div>
      </div>
      <div class="action-card-actions">
        <button
          type="button"
          class="btn btn-primary"
          data-testid="import-flag-list-button"
          onclick={() => void startFlagListImport()}
          disabled={importQueue.importing}
        >
          <Icon name="target" size={16} /> Import FP/FN list…
        </button>
        <span class="action-card-hint text-muted">Needs animal_id and ts columns</span>
      </div>
    </section>
  </div>

  {#if importQueue.error}
    <p class="banner banner-error import-error" data-testid="import-error">Import failed: {importQueue.error}</p>
  {/if}

  {#if flagProjects.length > 0}
    <section class="home-section" data-testid="flag-projects-section">
      <div class="section-header">
        <h2 class="section-heading">
          <button
            type="button"
            class="section-toggle"
            data-testid="flag-projects-section-toggle"
            aria-expanded={flagProjectsOpen}
            aria-controls="flag-projects-body"
            onclick={toggleFlagProjects}
          >
            <Icon name={flagProjectsOpen ? 'chevron-down' : 'chevron-right'} size={16} />
            <span class="section-title">FP/FN reviews</span>
            <span class="section-count" data-testid="flag-projects-count">{flagProjects.length}</span>
          </button>
        </h2>
        {#if flagProjectsOpen}
          <div class="section-actions">
            <button
              type="button"
              class="btn btn-sm btn-ghost btn-danger-text"
              data-testid="clear-all-flag-projects-button"
              title="Remove every FP/FN review from this list"
              onclick={() => clearFlagProjects()}
            >
              Clear all
            </button>
          </div>
        {/if}
      </div>
      {#if flagProjectsOpen}
        <div id="flag-projects-body" class="section-body">
          <FlagProjectList {now} />
        </div>
      {/if}
    </section>
  {/if}

  {#if recentFiles.length > 0}
    <section class="home-section">
      <div class="section-header">
        <h2 class="section-heading">
          <button
            type="button"
            class="section-toggle"
            data-testid="recent-files-section-toggle"
            aria-expanded={recentFilesOpen}
            aria-controls="recent-files-body"
            onclick={toggleRecentFiles}
          >
            <Icon name={recentFilesOpen ? 'chevron-down' : 'chevron-right'} size={16} />
            <span class="section-title">Recent files</span>
            <span class="section-count" data-testid="recent-files-count">{recentFiles.length}</span>
          </button>
        </h2>
        {#if recentFilesOpen}
          <div class="section-actions">
            <label class="select-all">
              <input
                type="checkbox"
                data-testid="select-all-recent-checkbox"
                checked={allSelected}
                onchange={toggleAll}
              />
              Select all
            </label>
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
            <button
              type="button"
              class="btn btn-sm btn-ghost btn-danger-text"
              data-testid="clear-selected-recent-button"
              disabled={selectedPaths.length === 0}
              onclick={clearSelected}
            >
              Clear selected{selectedPaths.length > 0 ? ` (${selectedPaths.length})` : ''}
            </button>
            <button
              type="button"
              class="btn btn-sm btn-ghost btn-danger-text"
              data-testid="clear-all-recent-button"
              onclick={clearAll}
            >
              Clear all
            </button>
          </div>
        {/if}
      </div>
      {#if recentFilesOpen}
        <div id="recent-files-body" class="section-body">
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
    </section>
  {/if}
</div>

<style>
  .welcome {
    display: flex;
    flex-direction: column;
    padding: var(--space-5) var(--space-4) var(--space-6);
    max-width: 960px;
    margin: 0 auto;
  }

  .welcome-hero {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: var(--space-2);
    padding-top: var(--space-4);
    margin-bottom: var(--space-5);
  }

  .welcome-mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 52px;
    height: 52px;
    border-radius: var(--radius-lg);
    background: var(--color-accent-soft);
    color: var(--color-accent);
    margin-bottom: var(--space-1);
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

  /* One card per way in: two side by side when there's room, stacked when not. */
  .action-cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: var(--space-4);
  }

  .action-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    padding: var(--space-4);
    border-radius: var(--radius-md);
    border: 1px solid var(--color-border);
    background: var(--color-surface);
  }

  .action-card-head {
    display: flex;
    align-items: flex-start;
    gap: var(--space-3);
  }

  .action-card-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 40px;
    height: 40px;
    border-radius: var(--radius-md);
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }

  .action-card-text {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    min-width: 0;
  }

  .action-card-title {
    font-size: var(--font-size-base);
    font-weight: 600;
    color: var(--color-text);
  }

  .action-card-desc {
    font-size: var(--font-size-sm);
  }

  /* Pinned to the card's bottom so both cards' buttons line up side by side. */
  .action-card-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-3);
    margin-top: auto;
  }

  .action-card-hint {
    font-size: 0.75rem;
  }

  .import-error {
    margin-top: var(--space-4);
  }

  /* Collapsible sections (FP/FN reviews, Recent files) share one header look. */
  .home-section {
    width: 100%;
    margin-top: var(--space-5);
  }

  .section-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2) var(--space-3);
  }

  .section-heading {
    margin-right: auto;
    font-size: inherit;
  }

  .section-toggle {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-2);
    margin-left: calc(-1 * var(--space-2));
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--color-text-muted);
    transition: background var(--transition-fast), color var(--transition-fast);
  }

  .section-toggle:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .section-title {
    font-size: var(--font-size-sm);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .section-count {
    min-width: 20px;
    padding: 0 6px;
    border-radius: 999px;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    color: var(--color-text-muted);
    font-size: 0.75rem;
    font-weight: 600;
    line-height: 18px;
    text-align: center;
  }

  .section-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2) var(--space-3);
  }

  .section-body {
    margin-top: var(--space-3);
  }

  .select-all {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
  }

  .recent-files-hint {
    margin: 0 0 var(--space-3);
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
