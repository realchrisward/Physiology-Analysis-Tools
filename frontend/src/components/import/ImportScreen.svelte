<script module lang="ts">
  // What the Home screen is narrowed to. Module state: it survives opening a
  // recording and coming back, but not a restart (the sort is remembered).
  export type HomeType = 'all' | 'recordings' | 'reviews'
  export type HomeSort = 'recent' | 'name'
  export const homeFilter: { query: string; type: HomeType } = $state({ query: '', type: 'all' })
</script>

<script lang="ts">
  import { onMount } from 'svelte'
  import { formatRelativeTime, recencyGroup, shortFolder, type RecencyGroup } from '../../lib/format'
  import { matchesQuery } from '../../lib/search'
  import { clearFlagProjects, flagProjects, flagStorage, startFlagListImport } from '../../lib/stores/flagProjects.svelte'
  import { fileRegistry } from '../../lib/stores/fileRegistry.svelte'
  import {
    handleImportFiles,
    handleImportFolder,
    handleStop,
    importQueue,
    openFiles,
    openRecentFile,
  } from '../../lib/stores/importQueue.svelte'
  import { clearRecentFiles, recentFiles, removeRecentFiles, type RecentFile } from '../../lib/stores/recentFiles.svelte'
  import FlagProjectList from '../flags/FlagProjectList.svelte'
  import Icon from '../shared/Icon.svelte'
  import SearchField from '../shared/SearchField.svelte'

  // Keeps "Opened 5 minutes ago" (and the Today / Yesterday groups) honest
  // while the screen stays open.
  let now: number = $state(Date.now())
  onMount(() => {
    const timer = setInterval(() => (now = Date.now()), 60_000)
    return () => clearInterval(timer)
  })

  // Remembered across restarts; a blocked/unavailable localStorage just
  // means the choice isn't remembered.
  const FLAG_PROJECTS_OPEN_KEY = 'pat.home.flagProjectsOpen'
  const RECENT_FILES_OPEN_KEY = 'pat.home.recentFilesOpen'
  const SORT_KEY = 'pat.home.sort'

  function load(key: string): string | null {
    try {
      return localStorage.getItem(key)
    } catch {
      return null
    }
  }

  function save(key: string, value: string) {
    try {
      localStorage.setItem(key, value)
    } catch {
      // ignore
    }
  }

  let flagProjectsOpen: boolean = $state(load(FLAG_PROJECTS_OPEN_KEY) !== 'false')
  let recentFilesOpen: boolean = $state(load(RECENT_FILES_OPEN_KEY) !== 'false')
  let sort: HomeSort = $state(load(SORT_KEY) === 'name' ? 'name' : 'recent')

  function toggleFlagProjects() {
    flagProjectsOpen = !flagProjectsOpen
    save(FLAG_PROJECTS_OPEN_KEY, String(flagProjectsOpen))
  }

  function toggleRecentFiles() {
    recentFilesOpen = !recentFilesOpen
    save(RECENT_FILES_OPEN_KEY, String(recentFilesOpen))
  }

  function setSort(value: HomeSort) {
    sort = value
    save(SORT_KEY, value)
  }

  // Nothing to come back to yet: the screen is a welcome instead of a list.
  let firstRun = $derived(recentFiles.length === 0 && flagProjects.length === 0)

  // ---- Search, type and sort ------------------------------------------------
  let searching = $derived(homeFilter.query.trim() !== '')

  const byName = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })

  let matchingRecents = $derived(recentFiles.filter((file) => matchesQuery(homeFilter.query, file.filename, file.path)))
  let matchingProjects = $derived(
    flagProjects.filter((project) =>
      matchesQuery(
        homeFilter.query,
        project.name,
        project.csvPath,
        project.animals.map((animal) => animal.animalId).join(' '),
      ),
    ),
  )

  let shownRecents = $derived.by(() => {
    if (homeFilter.type === 'reviews') return []
    // `recentFiles` is newest first already.
    return sort === 'name' ? [...matchingRecents].sort((a, b) => byName(a.filename, b.filename)) : matchingRecents
  })
  let shownProjects = $derived.by(() => {
    if (homeFilter.type === 'recordings') return []
    return sort === 'name'
      ? [...matchingProjects].sort((a, b) => byName(a.name, b.name))
      : [...matchingProjects].sort((a, b) => b.openedAt - a.openedAt)
  })

  // Rows under Today / Yesterday / Earlier this week / Older headings when
  // sorted by when they were opened; one plain list when sorted by name.
  let recentGroups = $derived.by(() => {
    if (sort === 'name') return [{ label: null as RecencyGroup | null, files: shownRecents }]
    const groups: { label: RecencyGroup | null; files: RecentFile[] }[] = []
    for (const file of shownRecents) {
      const label = recencyGroup(file.openedAt, now)
      const last = groups[groups.length - 1]
      if (last && last.label === label) last.files.push(file)
      else groups.push({ label, files: [file] })
    }
    return groups
  })

  let typeOptions = $derived([
    { id: 'all' as HomeType, label: 'All', count: matchingRecents.length + matchingProjects.length },
    { id: 'recordings' as HomeType, label: 'Recordings', count: matchingRecents.length },
    { id: 'reviews' as HomeType, label: 'FP/FN reviews', count: matchingProjects.length },
  ])

  let nothingShown = $derived(!firstRun && shownRecents.length === 0 && shownProjects.length === 0)

  // While searching, a folded section would hide its matches: both show.
  let projectsExpanded = $derived(flagProjectsOpen || searching)
  let recentsExpanded = $derived(recentFilesOpen || searching)

  function clearSearch() {
    homeFilter.query = ''
    homeFilter.type = 'all'
  }

  // ---- Selection --------------------------------------------------------------
  // Kept as paths and always read against what is shown, so a file that was
  // filtered out or dropped off the list can never be acted on by a stale tick.
  let ticked: string[] = $state([])
  let selectedPaths = $derived(shownRecents.map((f) => f.path).filter((p) => ticked.includes(p)))
  let allSelected = $derived(shownRecents.length > 0 && selectedPaths.length === shownRecents.length)
  let someSelected = $derived(selectedPaths.length > 0 && !allSelected)

  function toggleTicked(path: string) {
    ticked = ticked.includes(path) ? ticked.filter((p) => p !== path) : [...ticked, path]
  }

  function toggleAll() {
    const shown = shownRecents.map((f) => f.path)
    ticked = allSelected ? ticked.filter((p) => !shown.includes(p)) : [...new Set([...ticked, ...shown])]
  }

  // Opens the ticked files in the order shown; the first one is opened, the
  // others are ready in the sidebar.
  function openSelected() {
    void openFiles(selectedPaths)
  }

  // Taking files off this list never touches the files on disk, the
  // sidebar's list of loaded files, or saved review work.
  function removeSelected() {
    removeRecentFiles(selectedPaths)
    ticked = []
  }

  function removeOne(event: MouseEvent, path: string) {
    event.stopPropagation()
    removeRecentFiles([path])
    ticked = ticked.filter((p) => p !== path)
  }

  function clearAll() {
    clearRecentFiles()
    ticked = []
  }

  function inSession(path: string): boolean {
    return fileRegistry.some((row) => row.path === path)
  }

  // Dropping the projects mid-import would race the recordings still landing.
  function clearAllFlagProjects() {
    if (importQueue.importing) return
    clearFlagProjects()
  }
</script>

{#snippet importButtons(layout: 'header' | 'start')}
  <div class="import-buttons" class:import-buttons-start={layout === 'start'}>
    <button
      type="button"
      class="btn btn-primary"
      data-testid="import-files-button"
      onclick={handleImportFiles}
      disabled={importQueue.importing}
    >
      <Icon name="file-plus" size={16} /> Import files
    </button>
    <button
      type="button"
      class="btn"
      data-testid="import-folder-button"
      onclick={handleImportFolder}
      disabled={importQueue.importing}
    >
      <Icon name="folder" size={16} /> Import folder
    </button>
  </div>
{/snippet}

{#snippet autoRunToggle()}
  <label class="auto-run" title="Detect beats on each recording as soon as it is imported">
    <input
      type="checkbox"
      data-testid="auto-run-checkbox"
      bind:checked={importQueue.autoRun}
      disabled={importQueue.importing}
    />
    Detect beats automatically on import
  </label>
{/snippet}

{#snippet flagListButton(primary: boolean)}
  <button
    type="button"
    class="btn"
    class:btn-primary={primary}
    data-testid="import-flag-list-button"
    onclick={() => void startFlagListImport()}
    disabled={importQueue.importing}
  >
    <Icon name="target" size={16} /> Import FP/FN list…
  </button>
{/snippet}

<div class="home">
  {#if firstRun}
    <!-- First run: what the app is for, and the two ways in. -->
    <div class="welcome">
      <div class="welcome-mark"><Icon name="activity" size={26} /></div>
      <h1 class="welcome-title">Physiology Analysis Tools</h1>
      <p class="welcome-subtitle text-muted">Detect heartbeats in ECG recordings and review the arrhythmias.</p>
    </div>

    <div class="start-cards">
      <section class="start-card" aria-labelledby="start-ecg-title">
        <span class="start-icon"><Icon name="file-text" size={20} /></span>
        <h2 class="start-title" id="start-ecg-title">Review ECG recordings</h2>
        <p class="start-desc text-muted">Import recordings (.adicht, .txt, .edf, .mat) or a whole folder of them.</p>
        {@render importButtons('start')}
        {@render autoRunToggle()}
      </section>
      <section class="start-card" aria-labelledby="start-flag-title">
        <span class="start-icon"><Icon name="target" size={20} /></span>
        <h2 class="start-title" id="start-flag-title">Check a model's mistakes</h2>
        <p class="start-desc text-muted">
          Load an FP/FN list (a CSV with animal_id and ts). Matching recordings open zoomed to each flagged beat.
        </p>
        <div class="import-buttons import-buttons-start">{@render flagListButton(false)}</div>
      </section>
    </div>
  {:else}
    <header class="home-header">
      <div class="home-heading">
        <h1 class="home-title">Home</h1>
        <p class="text-muted home-subtitle">Pick up a recording or an FP/FN review, or import new ones.</p>
      </div>
      <div class="home-actions">
        <div class="home-action-row">
          {@render importButtons('header')}
          {@render flagListButton(false)}
        </div>
        {@render autoRunToggle()}
      </div>
    </header>
  {/if}

  {#if importQueue.detecting}
    <div class="banner detecting-banner" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <span>Detecting beats in the background…</span>
      <button type="button" class="btn btn-sm" data-testid="stop-button" onclick={handleStop}>Stop</button>
    </div>
  {/if}
  {#if importQueue.error}
    <p class="banner banner-error" data-testid="import-error">Import failed: {importQueue.error}</p>
  {/if}
  {#if flagStorage.error}
    <p class="banner banner-error" data-testid="flag-storage-error">{flagStorage.error}</p>
  {/if}

  {#if !firstRun}
    <div class="home-toolbar">
      <SearchField
        bind:value={homeFilter.query}
        placeholder="Search recordings, folders, animal IDs, FP/FN reviews…"
        label="Search Home"
        testid="home-search-input"
      />
      <div class="toolbar-row">
        <div class="type-chips" role="group" aria-label="Show">
          {#each typeOptions as option (option.id)}
            <button
              type="button"
              class="chip"
              class:chip-on={homeFilter.type === option.id}
              aria-pressed={homeFilter.type === option.id}
              data-testid={`home-type-${option.id}`}
              onclick={() => (homeFilter.type = option.id)}
            >
              {option.label} <span class="chip-count">{option.count}</span>
            </button>
          {/each}
        </div>
        <label class="sort">
          Sort
          <select
            data-testid="home-sort-select"
            value={sort}
            onchange={(event) => setSort((event.currentTarget as HTMLSelectElement).value as HomeSort)}
          >
            <option value="recent">Last opened</option>
            <option value="name">Name</option>
          </select>
        </label>
      </div>
    </div>
  {/if}

  {#if shownProjects.length > 0}
    <section class="home-section" data-testid="flag-projects-section">
      <div class="section-header">
        <h2 class="section-heading">
          <button
            type="button"
            class="section-toggle"
            data-testid="flag-projects-section-toggle"
            aria-expanded={projectsExpanded}
            aria-controls="flag-projects-body"
            onclick={toggleFlagProjects}
          >
            <Icon name={projectsExpanded ? 'chevron-down' : 'chevron-right'} size={16} />
            <span class="section-title">FP/FN reviews</span>
            <span class="section-count" data-testid="flag-projects-count">{shownProjects.length}</span>
          </button>
        </h2>
        {#if projectsExpanded && !searching}
          <button
            type="button"
            class="btn btn-sm btn-ghost btn-danger-text"
            data-testid="clear-all-flag-projects-button"
            title={importQueue.importing ? 'Wait for the import to finish' : 'Remove every FP/FN review from this list'}
            disabled={importQueue.importing}
            onclick={clearAllFlagProjects}
          >
            Clear list
          </button>
        {/if}
      </div>
      {#if projectsExpanded}
        <div id="flag-projects-body" class="section-body">
          <FlagProjectList {now} projects={shownProjects} />
        </div>
      {/if}
    </section>
  {/if}

  {#if shownRecents.length > 0}
    <section class="home-section" data-testid="recent-files-section">
      <div class="section-header">
        <h2 class="section-heading">
          <button
            type="button"
            class="section-toggle"
            data-testid="recent-files-section-toggle"
            aria-expanded={recentsExpanded}
            aria-controls="recent-files-body"
            onclick={toggleRecentFiles}
          >
            <Icon name={recentsExpanded ? 'chevron-down' : 'chevron-right'} size={16} />
            <span class="section-title">Recordings</span>
            <span class="section-count" data-testid="recent-files-count">{shownRecents.length}</span>
          </button>
        </h2>
        {#if recentsExpanded}
          <label class="select-all">
            <input
              type="checkbox"
              data-testid="select-all-recent-checkbox"
              checked={allSelected}
              {@attach (node: HTMLInputElement) => {
                node.indeterminate = someSelected
              }}
              onchange={toggleAll}
            />
            Select all{searching ? ' shown' : ''}
          </label>
          {#if !searching}
            <button
              type="button"
              class="btn btn-sm btn-ghost btn-danger-text"
              data-testid="clear-all-recent-button"
              title="Empty this list (files on disk and saved reviews are not touched)"
              onclick={clearAll}
            >
              Clear list
            </button>
          {/if}
        {/if}
      </div>

      {#if recentsExpanded}
        <div id="recent-files-body" class="section-body">
          {#each recentGroups as group (group.label ?? 'all')}
            {#if group.label}<h3 class="group-label">{group.label}</h3>{/if}
            <ul class="recent-list">
              {#each group.files as file (file.path)}
                {@const selected = ticked.includes(file.path)}
                <li class="recent-row" class:selected>
                  <input
                    type="checkbox"
                    class="recent-check"
                    data-testid="recent-file-checkbox"
                    aria-label={`Select ${file.filename}`}
                    checked={selected}
                    onchange={() => toggleTicked(file.path)}
                  />
                  <button
                    type="button"
                    class="recent-open"
                    data-testid="recent-file-card"
                    title={file.path}
                    onclick={() => openRecentFile(file.path)}
                    disabled={importQueue.importing}
                  >
                    <Icon name="file-text" size={16} />
                    <span class="recent-name">{file.filename}</span>
                    <span class="recent-folder text-muted">{shortFolder(file.path)}</span>
                    {#if inSession(file.path)}
                      <span class="recent-badge" title="Already loaded: it opens straight away">In sidebar</span>
                    {/if}
                    <span class="recent-time text-muted" data-testid="recent-file-opened"
                      >Opened {formatRelativeTime(file.openedAt, now)}</span
                    >
                  </button>
                  <button
                    type="button"
                    class="icon-btn recent-remove"
                    data-testid="recent-file-remove-button"
                    aria-label={`Remove ${file.filename} from this list`}
                    title="Remove from this list (the file itself is not touched)"
                    onclick={(event) => removeOne(event, file.path)}
                  >
                    <Icon name="x" size={14} />
                  </button>
                </li>
              {/each}
            </ul>
          {/each}

          {#if selectedPaths.length > 0}
            <div class="selection-bar" data-testid="recent-selection-bar" role="region" aria-label="Selected recordings">
              <span class="selection-count">{selectedPaths.length} selected</span>
              <button
                type="button"
                class="btn btn-sm btn-primary"
                data-testid="open-selected-button"
                disabled={importQueue.importing}
                onclick={openSelected}
              >
                Open {selectedPaths.length}
              </button>
              <button
                type="button"
                class="btn btn-sm"
                data-testid="clear-selected-recent-button"
                onclick={removeSelected}
              >
                Remove from list
              </button>
              <button type="button" class="btn btn-sm btn-ghost" data-testid="clear-selection-button" onclick={() => (ticked = [])}>
                Clear selection
              </button>
            </div>
          {/if}
        </div>
      {/if}
    </section>
  {/if}

  {#if nothingShown}
    <div class="no-results" data-testid="home-no-results">
      <Icon name="search" size={20} />
      <p>
        {#if searching}
          Nothing matches “{homeFilter.query.trim()}”{homeFilter.type !== 'all' ? ' here' : ''}.
        {:else if homeFilter.type === 'recordings'}
          No recent recordings.
        {:else}
          No FP/FN reviews yet.
        {/if}
      </p>
      <button type="button" class="btn btn-sm" data-testid="home-clear-search-button" onclick={clearSearch}>
        Show everything
      </button>
    </div>
  {/if}
</div>

<style>
  .home {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    padding: var(--space-5) var(--space-4) var(--space-6);
    max-width: 960px;
    margin: 0 auto;
  }

  /* ---- First run ---- */
  .welcome {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: var(--space-2);
    padding-top: var(--space-5);
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
  }

  .welcome-title {
    font-size: var(--font-size-xl);
    font-weight: 600;
  }

  .welcome-subtitle {
    max-width: 440px;
  }

  .start-cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: var(--space-4);
  }

  .start-card {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-4);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }

  .start-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    border-radius: var(--radius-md);
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }

  .start-title {
    font-size: var(--font-size-base);
    font-weight: 600;
  }

  .start-desc {
    font-size: var(--font-size-sm);
    margin: 0;
  }

  /* ---- Returning user ---- */
  .home-header {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-3) var(--space-4);
  }

  .home-title {
    font-size: var(--font-size-xl);
    font-weight: 600;
  }

  .home-subtitle {
    margin: 2px 0 0;
    font-size: var(--font-size-sm);
  }

  .home-actions {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: var(--space-2);
  }

  .home-action-row,
  .import-buttons {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .import-buttons-start {
    margin-top: auto;
  }

  .auto-run {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.8125rem;
    color: var(--color-text-muted);
  }

  .detecting-banner {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    border: 1px solid var(--color-border);
    background: var(--color-surface);
  }

  .detecting-banner .btn {
    margin-left: auto;
  }

  .spinner {
    display: inline-block;
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

  .banner {
    margin: 0;
  }

  .home-toolbar {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .toolbar-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
  }

  .type-chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: 3px var(--space-3);
    border: 1px solid var(--color-border);
    border-radius: 999px;
    background: var(--color-surface);
    color: var(--color-text-muted);
    font-size: 0.8125rem;
    font-weight: 500;
    transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
  }

  .chip:hover {
    color: var(--color-text);
    background: var(--color-surface-hover);
  }

  .chip-on,
  .chip-on:hover {
    border-color: var(--color-accent);
    background: var(--color-accent-soft);
    color: var(--color-text);
  }

  .chip-count {
    font-variant-numeric: tabular-nums;
    color: var(--color-text-muted);
  }

  .sort {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.8125rem;
    color: var(--color-text-muted);
  }

  .sort select {
    padding: 3px var(--space-2);
    font-size: 0.8125rem;
  }

  /* Sections (FP/FN reviews, Recordings) share one header look. */
  .home-section {
    width: 100%;
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

  .section-body {
    margin-top: var(--space-2);
  }

  .select-all {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
  }

  .group-label {
    margin: var(--space-3) 0 var(--space-1);
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--color-text-muted);
  }

  .group-label:first-child {
    margin-top: 0;
  }

  .recent-list {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    overflow: hidden;
    container-type: inline-size;
  }

  .recent-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 0 var(--space-2) 0 var(--space-3);
    transition: background var(--transition-fast);
  }

  .recent-row + .recent-row {
    border-top: 1px solid var(--color-border);
  }

  .recent-row:hover,
  .recent-row.selected {
    background: var(--color-surface-hover);
  }

  .recent-check {
    flex-shrink: 0;
  }

  .recent-open {
    flex: 1;
    min-width: 0;
    display: grid;
    grid-template-columns: auto minmax(0, 1.2fr) minmax(0, 1fr) auto auto;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-1);
    border: none;
    background: transparent;
    color: var(--color-text);
    font-size: var(--font-size-sm);
    text-align: left;
  }

  .recent-open:disabled {
    opacity: 0.6;
  }

  .recent-open :global(svg) {
    color: var(--color-text-muted);
  }

  .recent-name,
  .recent-folder {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .recent-name {
    font-weight: 500;
  }

  .recent-folder,
  .recent-time {
    font-size: 0.75rem;
  }

  .recent-time {
    white-space: nowrap;
    text-align: right;
  }

  .recent-badge {
    padding: 1px var(--space-2);
    border-radius: 999px;
    background: var(--color-accent-soft);
    color: var(--color-accent);
    font-size: 0.7rem;
    font-weight: 600;
    white-space: nowrap;
  }

  .recent-remove {
    flex-shrink: 0;
    width: 26px;
    height: 26px;
    opacity: 0;
  }

  .recent-row:hover .recent-remove,
  .recent-row:focus-within .recent-remove {
    opacity: 1;
  }

  /* Narrow: the folder goes under the name. */
  @container (max-width: 560px) {
    .recent-open {
      grid-template-columns: auto minmax(0, 1fr) auto;
    }

    .recent-folder {
      grid-column: 2 / 4;
      grid-row: 2;
    }

    .recent-badge {
      display: none;
    }
  }

  .selection-bar {
    position: sticky;
    bottom: var(--space-3);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
    margin-top: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-md);
    background: var(--color-surface-raised);
    box-shadow: var(--shadow-md);
  }

  .selection-count {
    margin-right: auto;
    font-size: var(--font-size-sm);
    font-weight: 600;
  }

  .no-results {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-6) var(--space-4);
    color: var(--color-text-muted);
    text-align: center;
  }

  .no-results p {
    margin: 0;
  }
</style>
