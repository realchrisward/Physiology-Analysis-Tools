<script lang="ts">
  import { onMount } from 'svelte'
  import { matchFlagListFiles, parseFlagList } from '../../lib/api/flagLists'
  import {
    buildAnimals,
    getFlagProject,
    openFlagProject,
    saveFlagProject,
    type FlagProject,
    type FlagProjectAnimal,
  } from '../../lib/stores/flagProjects.svelte'
  import { importQueue } from '../../lib/stores/importQueue.svelte'
  import Icon from '../shared/Icon.svelte'
  import FlagCountPill from './FlagCountPill.svelte'

  // Imports an FP/FN list (a CSV) in two steps — pick which animals to review,
  // then point at the folders their recordings live in — and opens the result.
  //   csvPath   - a newly picked list to import (projectId is null), or
  //   projectId - an existing project to edit, e.g. to find missing recordings
  //               (csvPath is null); starts at step 2.
  //   onClose   - called when the dialog should close (cancel or after opening).
  let {
    csvPath = null,
    projectId = null,
    onClose,
  }: {
    csvPath?: string | null
    projectId?: string | null
    onClose: () => void
  } = $props()

  let editing = $derived(!csvPath && !!projectId)

  // Reading the CSV happens on mount; editing starts from a copy of the saved
  // project. Either way the dialog works on `draft` and only writes it back
  // (saveFlagProject) when the technician presses Open, so Cancel is always a
  // clean walk-away.
  type Phase = 'reading' | 'error' | 'ready'
  let phase: Phase = $state(initialPhase())
  let loadError: string | null = $state(null)
  let draft: FlagProject | null = $state(editedProject())
  let step: 1 | 2 = $state(initialStep())
  // Rows the CSV had but that couldn't be used (blank ID / unreadable ts).
  let skippedRows = $state(0)

  function initialStep(): 1 | 2 {
    return projectId && !csvPath ? 2 : 1
  }

  function initialPhase(): Phase {
    return projectId && !csvPath && getFlagProject(projectId) ? 'ready' : 'reading'
  }

  function editedProject(): FlagProject | null {
    if (!projectId || csvPath) return null
    const project = getFlagProject(projectId)
    return project ? JSON.parse(JSON.stringify(project)) : null
  }

  function fileName(path: string): string {
    return path.split(/[\\/]/).pop() || path
  }

  async function loadList(path: string) {
    const result = await parseFlagList(path)
    if (result.status === 'error' || !result.rows) {
      loadError = result.error || 'This list could not be read.'
      phase = 'error'
      return
    }
    const animals = buildAnimals(result.rows)
    if (animals.length === 0) {
      loadError = 'This list has no usable rows. It needs an animal_id column and a ts column.'
      phase = 'error'
      return
    }

    // Importing a list that is already in Home's list again keeps what the
    // technician set up last time: its folders and which animals they chose.
    const existing = getFlagProject(path)
    if (existing) {
      const chosen = new Map(existing.animals.map((animal) => [animal.animalId, animal.selected]))
      for (const animal of animals) animal.selected = chosen.get(animal.animalId) ?? true
    }
    draft = {
      id: path,
      csvPath: path,
      name: result.filename || fileName(path),
      folders: existing ? [...existing.folders] : [],
      animals,
      createdAt: existing?.createdAt ?? Date.now(),
      openedAt: Date.now(),
    }
    skippedRows = result.skipped_rows ?? 0
    phase = 'ready'
  }

  onMount(() => {
    if (csvPath) {
      void loadList(csvPath)
    } else if (draft) {
      if (draft.folders.length > 0) void runMatch()
    } else {
      // Editing a project that has since been removed from Home's list.
      loadError = 'This FP/FN list is no longer in your list.'
      phase = 'error'
    }
  })

  // ---- Step 1: choose animals ----

  let filter = $state('')
  let animals = $derived(draft?.animals ?? [])
  let visibleAnimals = $derived.by(() => {
    const needle = filter.trim().toLowerCase()
    return needle ? animals.filter((animal) => animal.animalId.toLowerCase().includes(needle)) : animals
  })
  let selectedIds = $derived(animals.filter((animal) => animal.selected).map((animal) => animal.animalId))
  let allVisibleSelected = $derived(visibleAnimals.length > 0 && visibleAnimals.every((animal) => animal.selected))

  // Whole-list figures for the summary line (not just the chosen animals).
  let totals = $derived.by(() => {
    let flags = 0
    let fp = 0
    let fn = 0
    for (const animal of animals) {
      for (const flag of animal.flags) {
        flags += 1
        if (flag.kind === 'fp') fp += 1
        else if (flag.kind === 'fn') fn += 1
      }
    }
    return { flags, fp, fn }
  })

  function kindCounts(animal: FlagProjectAnimal): { fp: number; fn: number } {
    let fp = 0
    let fn = 0
    for (const flag of animal.flags) {
      if (flag.kind === 'fp') fp += 1
      else if (flag.kind === 'fn') fn += 1
    }
    return { fp, fn }
  }

  // Acts on what the filter currently shows, so "filter, then select all"
  // picks just those animals.
  function toggleAllVisible() {
    const target = !allVisibleSelected
    for (const animal of visibleAnimals) animal.selected = target
  }

  function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
    return count === 1 ? singular : pluralForm
  }

  let nextLabel = $derived.by(() => {
    const count = selectedIds.length
    if (count === 0) return 'Next: find recordings'
    if (count === animals.length && count > 1) return `Next: find recordings for all ${count} animals`
    return `Next: find recordings for ${count} ${plural(count, 'animal')}`
  })

  function goToFolders() {
    if (selectedIds.length === 0) return
    step = 2
    void runMatch()
  }

  // ---- Step 2: find recordings ----

  // Animal ID -> every recording found for it (first is the one used).
  let matches: Record<string, string[]> = $state({})
  let missingFolders: string[] = $state([])
  type MatchState = 'idle' | 'searching' | 'done' | 'error'
  let matchState = $state<MatchState>('idle')
  let matchError: string | null = $state(null)
  let showMissing = $state(false)
  let showMatched = $state(false)
  let pickingFolder = $state(false)

  // Out-of-order guard, same idea as `detectionRequestId` in ReviewWorkspace:
  // adding folders quickly starts overlapping searches, and only the newest
  // one may fill `matches`.
  let matchRequestId = 0
  // What the current `matches` was searched for, so going Back and Next again
  // without changing anything doesn't rescan every folder.
  let lastMatchKey = ''

  let foundIds = $derived(selectedIds.filter((id) => (matches[id]?.length ?? 0) > 0))
  let missingIds = $derived(selectedIds.filter((id) => (matches[id]?.length ?? 0) === 0))
  let searching = $derived(matchState === 'searching')

  async function runMatch() {
    if (!draft) return
    const ids = [...selectedIds]
    const folders = [...draft.folders]
    const key = JSON.stringify([ids, folders])
    if (key === lastMatchKey && matchState !== 'error') return
    lastMatchKey = key
    const requestId = ++matchRequestId

    if (ids.length === 0 || folders.length === 0) {
      matches = {}
      missingFolders = []
      matchError = null
      matchState = 'idle'
      return
    }

    matchState = 'searching'
    matchError = null
    const result = await matchFlagListFiles(ids, folders)
    if (requestId !== matchRequestId) return // superseded by a newer search

    if (result.status === 'error') {
      matches = {}
      missingFolders = []
      matchError = result.error || 'The folders could not be searched.'
      matchState = 'error'
      return
    }
    const found: Record<string, string[]> = {}
    for (const match of result.matches ?? []) {
      if (match.paths.length > 0) found[match.animal_id] = match.paths
    }
    matches = found
    missingFolders = result.missing_folders ?? []
    matchState = 'done'
  }

  async function addFolder() {
    if (!draft || pickingFolder) return
    pickingFolder = true
    try {
      const folder = (await window.api?.pickDirectory()) ?? null
      if (!folder || !draft || draft.folders.includes(folder)) return
      draft.folders.push(folder)
      void runMatch()
    } finally {
      pickingFolder = false
    }
  }

  function removeFolder(folder: string) {
    if (!draft) return
    draft.folders = draft.folders.filter((candidate) => candidate !== folder)
    void runMatch()
  }

  function handleOpen() {
    if (!draft || foundIds.length === 0 || searching) return
    // Selected animals get their recording; everything else is left unmatched.
    for (const animal of draft.animals) {
      animal.filePath = animal.selected ? (matches[animal.animalId]?.[0] ?? null) : null
    }
    const project = $state.snapshot(draft) as FlagProject
    saveFlagProject(project)
    onClose()
    void openFlagProject(project.id)
  }

  // ---- Dialog chrome ----

  let dialogEl: HTMLDivElement | undefined = $state()

  onMount(() => {
    dialogEl?.focus()

    function handleKeydown(event: KeyboardEvent) {
      if (event.key !== 'Escape' || pickingFolder) return
      onClose()
    }
    document.addEventListener('keydown', handleKeydown)
    return () => document.removeEventListener('keydown', handleKeydown)
  })

  // Only closes when the backdrop itself (not a click bubbling up from the
  // card) was the actual click target.
  function handleBackdropClick(event: MouseEvent) {
    if (event.target === event.currentTarget) onClose()
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- Escape is handled globally via the onMount keydown listener above; this
     backdrop only needs to catch a direct click on itself to dismiss, so it is
     deliberately not a focusable control. -->
<div class="flag-backdrop" data-testid="flag-import-backdrop" onclick={handleBackdropClick}>
  <div
    class="flag-dialog"
    data-testid="flag-import-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="flag-dialog-title"
    tabindex="-1"
    bind:this={dialogEl}
  >
    <div class="flag-header">
      <div class="flag-header-text">
        <h2 id="flag-dialog-title">{editing ? 'Find recordings' : 'Import FP/FN list'}</h2>
        {#if phase === 'ready'}
          <ol class="flag-steps" aria-label="Steps">
            <li class="flag-step" class:current={step === 1} class:done={step > 1} aria-current={step === 1 ? 'step' : undefined}>
              <span class="flag-step-num">1</span> Choose animals
            </li>
            <li class="flag-step-sep" aria-hidden="true"></li>
            <li class="flag-step" class:current={step === 2} aria-current={step === 2 ? 'step' : undefined}>
              <span class="flag-step-num">2</span> Find recordings
            </li>
          </ol>
        {/if}
      </div>
      <button
        type="button"
        class="icon-btn"
        data-testid="flag-close-button"
        onclick={onClose}
        aria-label="Close"
      >
        <Icon name="x" />
      </button>
    </div>

    <div class="flag-body">
      {#if phase === 'reading'}
        <p class="flag-status text-muted" data-testid="flag-dialog-reading">
          <span class="spinner" aria-hidden="true"></span> Reading list…
        </p>
      {:else if phase === 'error'}
        <p class="banner banner-error" role="alert" data-testid="flag-dialog-error">{loadError}</p>
      {:else if draft && step === 1}
        <div class="flag-step-panel" data-testid="flag-dialog-step-animals">
          <div class="flag-summary">
            <strong class="flag-summary-name" title={draft.csvPath}>{draft.name}</strong>
            <span class="flag-summary-stats text-muted">
              {totals.flags} flagged {plural(totals.flags, 'beat')} · {animals.length}
              {plural(animals.length, 'animal')}
              {#if totals.fp > 0}<FlagCountPill kind="fp" count={totals.fp} long />{/if}
              {#if totals.fn > 0}<FlagCountPill kind="fn" count={totals.fn} long />{/if}
              {#if skippedRows > 0}
                · {skippedRows} {plural(skippedRows, 'row')} skipped
              {/if}
            </span>
          </div>

          <div class="flag-list-controls">
            <div class="flag-filter">
              <span class="flag-filter-icon"><Icon name="search" size={14} /></span>
              <input
                type="text"
                class="flag-filter-input"
                data-testid="flag-animal-filter"
                placeholder="Filter animal IDs…"
                aria-label="Filter animal IDs"
                autocomplete="off"
                spellcheck="false"
                bind:value={filter}
              />
            </div>
            <label class="flag-select-all">
              <input
                type="checkbox"
                data-testid="flag-select-all"
                checked={allVisibleSelected}
                disabled={visibleAnimals.length === 0}
                onchange={toggleAllVisible}
              />
              {filter.trim() ? `Select all shown (${visibleAnimals.length})` : 'Select all'}
            </label>
            <span class="flag-selected-count text-muted" data-testid="flag-selected-count">
              {selectedIds.length} of {animals.length} selected
            </span>
          </div>

          <div class="flag-animal-list" data-testid="flag-animal-list">
            {#each visibleAnimals as animal (animal.animalId)}
              {@const counts = kindCounts(animal)}
              <label class="flag-animal-row" class:unselected={!animal.selected}>
                <input
                  type="checkbox"
                  data-testid="flag-animal-checkbox"
                  aria-label={`Include ${animal.animalId}`}
                  checked={animal.selected}
                  onchange={() => (animal.selected = !animal.selected)}
                />
                <span class="flag-animal-id">{animal.animalId}</span>
                <span class="flag-animal-count text-muted">
                  {animal.flags.length} flagged {plural(animal.flags.length, 'beat')}
                </span>
                <span class="flag-animal-badges">
                  {#if counts.fp > 0}<FlagCountPill kind="fp" count={counts.fp} />{/if}
                  {#if counts.fn > 0}<FlagCountPill kind="fn" count={counts.fn} />{/if}
                </span>
              </label>
            {:else}
              <p class="flag-empty text-muted">No animal IDs match “{filter.trim()}”.</p>
            {/each}
          </div>
        </div>
      {:else if draft}
        <div class="flag-step-panel" data-testid="flag-dialog-step-folders">
          <p class="flag-help text-muted">
            Recordings are matched by file name — animal ID <code>M00588436</code> matches
            <code>M00588436.adicht</code> (any supported format, sub-folders included). Add every folder that holds
            them.
          </p>

          {#if draft.folders.length === 0}
            <div class="flag-folders-empty">
              <Icon name="folder" size={28} />
              <p>No folders added yet.</p>
              <button
                type="button"
                class="btn btn-primary"
                data-testid="flag-add-folder-button"
                disabled={pickingFolder}
                onclick={addFolder}
              >
                <Icon name="plus" size={16} /> Add folder…
              </button>
            </div>
          {:else}
            <ul class="flag-folder-list">
              {#each draft.folders as folder (folder)}
                <li class="flag-folder-row" data-testid="flag-folder-row">
                  <Icon name="folder" size={16} />
                  <span class="flag-folder-path" title={folder}>{folder}</span>
                  <button
                    type="button"
                    class="icon-btn"
                    data-testid="flag-remove-folder-button"
                    aria-label={`Remove folder ${folder}`}
                    title="Remove folder"
                    onclick={() => removeFolder(folder)}
                  >
                    <Icon name="x" size={16} />
                  </button>
                </li>
              {/each}
            </ul>
            <div>
              <button
                type="button"
                class="btn btn-sm"
                data-testid="flag-add-folder-button"
                disabled={pickingFolder}
                onclick={addFolder}
              >
                <Icon name="plus" size={14} /> Add folder…
              </button>
            </div>

            <div class="flag-match-summary" data-testid="flag-match-summary" aria-live="polite">
              {#if searching}
                <p class="flag-status text-muted" data-testid="flag-searching">
                  <span class="spinner" aria-hidden="true"></span> Searching…
                </p>
              {:else if matchState === 'error'}
                <p class="banner banner-error" role="alert" data-testid="flag-match-error">{matchError}</p>
                <div>
                  <button type="button" class="btn btn-sm" data-testid="flag-retry-button" onclick={runMatch}>
                    Try again
                  </button>
                </div>
              {:else}
                <p class="flag-found-line" data-testid="flag-found-line">
                  Found {foundIds.length} of {selectedIds.length} {plural(selectedIds.length, 'recording')}
                </p>
                <div
                  class="flag-progress"
                  role="progressbar"
                  aria-label="Recordings found"
                  aria-valuemin="0"
                  aria-valuemax={selectedIds.length}
                  aria-valuenow={foundIds.length}
                >
                  <div
                    class="flag-progress-fill"
                    style:width={`${selectedIds.length ? (foundIds.length / selectedIds.length) * 100 : 0}%`}
                  ></div>
                </div>

                {#if missingFolders.length > 0}
                  <p class="flag-warning" data-testid="flag-missing-folders">
                    <Icon name="alert-triangle" size={16} />
                    <span>
                      {plural(missingFolders.length, 'This folder', 'These folders')} could not be read:
                      {missingFolders.join(', ')}
                    </span>
                  </p>
                {/if}

                {#if missingIds.length > 0}
                  <div class="flag-warning">
                    <Icon name="alert-triangle" size={16} />
                    <span>
                      {missingIds.length}
                      {plural(missingIds.length, 'animal has', 'animals have')} no recording in these folders
                    </span>
                    <button
                      type="button"
                      class="btn btn-sm"
                      data-testid="flag-add-another-folder-button"
                      disabled={pickingFolder}
                      onclick={addFolder}
                    >
                      Add another folder…
                    </button>
                  </div>
                  <button
                    type="button"
                    class="flag-disclosure"
                    aria-expanded={showMissing}
                    data-testid="flag-missing-toggle"
                    onclick={() => (showMissing = !showMissing)}
                  >
                    <Icon name={showMissing ? 'chevron-down' : 'chevron-right'} size={14} />
                    {showMissing ? 'Hide missing IDs' : 'Show missing IDs'}
                  </button>
                  {#if showMissing}
                    <ul class="flag-missing-list" data-testid="flag-missing-list">
                      {#each missingIds as id (id)}
                        <li class="flag-chip">{id}</li>
                      {/each}
                    </ul>
                  {/if}
                {:else if selectedIds.length > 0}
                  <p class="flag-all-found text-success">
                    <Icon name="check-circle" size={16} /> Every selected animal has a recording.
                  </p>
                {/if}

                {#if foundIds.length > 0}
                  <button
                    type="button"
                    class="flag-disclosure"
                    aria-expanded={showMatched}
                    data-testid="flag-matched-toggle"
                    onclick={() => (showMatched = !showMatched)}
                  >
                    <Icon name={showMatched ? 'chevron-down' : 'chevron-right'} size={14} />
                    {showMatched ? 'Hide matched recordings' : 'Show matched recordings'}
                  </button>
                  {#if showMatched}
                    <ul class="flag-matched-list" data-testid="flag-matched-list">
                      {#each foundIds as id (id)}
                        <li class="flag-matched-row">
                          <span class="flag-animal-id">{id}</span>
                          <span class="flag-matched-arrow text-muted" aria-hidden="true">→</span>
                          <span class="flag-matched-file" title={matches[id][0]}>{fileName(matches[id][0])}</span>
                          {#if matches[id].length > 1}
                            <span class="flag-matched-more text-muted" title={matches[id].slice(1).join('\n')}>
                              +{matches[id].length - 1} more (using the first)
                            </span>
                          {/if}
                        </li>
                      {/each}
                    </ul>
                  {/if}
                {/if}
              {/if}
            </div>
          {/if}
        </div>
      {/if}
    </div>

    <div class="flag-footer">
      {#if phase === 'ready' && step === 2}
        <button type="button" class="btn" data-testid="flag-back-button" onclick={() => (step = 1)}>
          <Icon name="chevron-left" size={14} /> Back
        </button>
      {/if}
      <span class="flag-footer-spacer"></span>
      {#if phase === 'error'}
        <button type="button" class="btn" data-testid="flag-error-close-button" onclick={onClose}>Close</button>
      {:else}
        <button type="button" class="btn" data-testid="flag-cancel-button" onclick={onClose}>Cancel</button>
        {#if phase === 'ready' && step === 1}
          <button
            type="button"
            class="btn btn-primary"
            data-testid="flag-next-button"
            disabled={selectedIds.length === 0}
            onclick={goToFolders}
          >
            {nextLabel}
          </button>
        {:else if phase === 'ready'}
          <button
            type="button"
            class="btn btn-primary"
            data-testid="flag-open-button"
            disabled={foundIds.length === 0 || searching || importQueue.importing}
            onclick={handleOpen}
          >
            {foundIds.length > 0
              ? `Open ${foundIds.length} ${plural(foundIds.length, 'recording')}`
              : 'Open recordings'}
          </button>
        {/if}
      {/if}
    </div>
  </div>
</div>

<style>
  .flag-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-5);
    z-index: 100;
  }

  .flag-dialog {
    width: 100%;
    max-width: 760px;
    max-height: 85vh;
    display: flex;
    flex-direction: column;
    background: var(--color-surface-raised);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
  }

  .flag-dialog:focus {
    outline: none;
  }

  .flag-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-4) var(--space-5);
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .flag-header h2 {
    font-size: var(--font-size-lg);
    font-weight: 600;
  }

  .flag-body {
    padding: var(--space-4) var(--space-5);
    overflow-y: auto;
    flex: 1;
  }

  .flag-footer {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-4) var(--space-5);
    border-top: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .flag-footer-spacer {
    flex: 1;
  }

  /* Step indicator */
  .flag-steps {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin: var(--space-2) 0 0;
    padding: 0;
    list-style: none;
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
  }

  .flag-step {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
  }

  .flag-step.current {
    color: var(--color-text);
    font-weight: 600;
  }

  .flag-step-num {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 1px solid var(--color-border);
    font-size: 0.75rem;
    font-weight: 600;
  }

  .flag-step.current .flag-step-num {
    background: var(--color-accent);
    border-color: var(--color-accent);
    color: var(--color-bg);
  }

  .flag-step.done .flag-step-num {
    border-color: var(--color-accent);
    color: var(--color-accent);
  }

  .flag-step-sep {
    width: 24px;
    height: 1px;
    background: var(--color-border);
  }

  .flag-step-panel {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .flag-status {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid var(--color-border);
    border-top-color: var(--color-accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    vertical-align: middle;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* Step 1 */
  .flag-summary {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .flag-summary-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .flag-summary-stats {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1) var(--space-2);
    font-size: var(--font-size-sm);
  }

  .flag-list-controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2) var(--space-4);
  }

  .flag-filter {
    position: relative;
    flex: 1 1 200px;
    min-width: 160px;
  }

  .flag-filter-icon {
    position: absolute;
    top: 50%;
    left: var(--space-3);
    transform: translateY(-50%);
    display: flex;
    color: var(--color-text-muted);
    pointer-events: none;
  }

  .flag-filter-input {
    width: 100%;
    padding-left: calc(var(--space-3) + 14px + var(--space-2));
  }

  .flag-select-all {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--font-size-sm);
    color: var(--color-text);
  }

  .flag-selected-count {
    font-size: var(--font-size-sm);
  }

  .flag-animal-list {
    max-height: 45vh;
    overflow-y: auto;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }

  .flag-animal-row {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--color-border);
    cursor: pointer;
    transition: background var(--transition-fast);
  }

  .flag-animal-row:last-child {
    border-bottom: none;
  }

  .flag-animal-row:hover {
    background: var(--color-surface-hover);
  }

  .flag-animal-row.unselected .flag-animal-id,
  .flag-animal-row.unselected .flag-animal-count {
    opacity: 0.6;
  }

  .flag-animal-id {
    font-family: var(--font-mono);
    font-size: var(--font-size-sm);
    color: var(--color-text);
  }

  .flag-animal-count {
    flex: 1;
    min-width: 0;
    font-size: 0.75rem;
  }

  .flag-animal-badges {
    display: inline-flex;
    gap: var(--space-1);
  }

  .flag-empty {
    padding: var(--space-4);
    text-align: center;
    font-size: var(--font-size-sm);
  }

  /* Step 2 */
  .flag-help {
    font-size: var(--font-size-sm);
  }

  .flag-help code {
    font-family: var(--font-mono);
    color: var(--color-text);
  }

  .flag-folders-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-5) var(--space-4);
    border: 1px dashed var(--color-border);
    border-radius: var(--radius-md);
    color: var(--color-text-muted);
    text-align: center;
  }

  .flag-folder-list {
    display: flex;
    flex-direction: column;
    margin: 0;
    padding: 0;
    list-style: none;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }

  .flag-folder-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-2) var(--space-1) var(--space-3);
    border-bottom: 1px solid var(--color-border);
    color: var(--color-text-muted);
  }

  .flag-folder-row:last-child {
    border-bottom: none;
  }

  .flag-folder-path {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
    font-size: var(--font-size-sm);
    color: var(--color-text);
  }

  .flag-match-summary {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-4);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }

  .flag-found-line {
    font-size: var(--font-size-lg);
    font-weight: 600;
    color: var(--color-text);
  }

  .flag-progress {
    width: 100%;
    height: 6px;
    border-radius: 999px;
    background: var(--color-border);
    overflow: hidden;
  }

  .flag-progress-fill {
    height: 100%;
    border-radius: 999px;
    background: var(--color-accent);
    transition: width var(--transition-fast);
  }

  .flag-warning {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2) var(--space-3);
    font-size: var(--font-size-sm);
    color: var(--color-warning);
  }

  .flag-all-found {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--font-size-sm);
  }

  .flag-disclosure {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-2);
    margin-left: calc(-1 * var(--space-2));
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    font-weight: 500;
  }

  .flag-disclosure:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .flag-missing-list {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    width: 100%;
    max-height: 25vh;
    overflow-y: auto;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .flag-chip {
    padding: 2px var(--space-2);
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-text);
  }

  .flag-matched-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    width: 100%;
    max-height: 30vh;
    overflow-y: auto;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .flag-matched-row {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    min-width: 0;
    font-size: var(--font-size-sm);
  }

  .flag-matched-file {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-text);
  }

  .flag-matched-more {
    flex-shrink: 0;
    font-size: 0.75rem;
  }
</style>
