<script module lang="ts">
  // The sidebar's filter, kept for the session (not across restarts): it
  // survives the sidebar collapsing and the screen changing under it.
  export type StatusFilter = 'all' | 'ready' | 'progress' | 'problems'
  export const sidebarFilter: { query: string; status: StatusFilter } = $state({ query: '', status: 'all' })
</script>

<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { formatRelativeTime } from '../../lib/format'
  import { matchesQuery } from '../../lib/search'
  import {
    clearFileRegistry,
    fileRegistry,
    removeFile,
    type FileRow,
    type ReviewSelection,
  } from '../../lib/stores/fileRegistry.svelte'
  import {
    formatEta,
    handleImportFiles,
    handleImportFolder,
    handleStop,
    importQueue,
    openRecentFile,
  } from '../../lib/stores/importQueue.svelte'
  import {
    closeFlagProject,
    openFlagProjects,
    openProjectPaths,
    projectRecordings,
    startFlagListImport,
    type FlagProject,
    type FlagProjectAnimal,
  } from '../../lib/stores/flagProjects.svelte'
  import { recentFiles } from '../../lib/stores/recentFiles.svelte'
  import Icon from '../shared/Icon.svelte'
  import MenuButton, { type MenuItem } from '../shared/MenuButton.svelte'
  import SearchField from '../shared/SearchField.svelte'
  import SidebarProjectTree from './SidebarProjectTree.svelte'
  import SidebarRowStatus from './SidebarRowStatus.svelte'
  import SidebarStatusDot from './SidebarStatusDot.svelte'

  let {
    collapsed = false,
    activePath = null,
    onReview,
    onResize,
    onFilesCleared,
    onFileRemoved,
    onSettingsOpen,
    onAboutOpen,
    onRequestExpand,
  }: {
    collapsed?: boolean
    activePath?: string | null
    onReview?: (selection: ReviewSelection) => void
    onResize?: (width: number) => void
    // Clearing the list while a file is open would leave the review
    // workspace showing something no longer listed anywhere, so the app
    // shell is told to go back to the welcome screen.
    onFilesCleared?: () => void
    // Removing the file that is currently open leaves the same problem, so
    // the shell is told which one went.
    onFileRemoved?: (path: string) => void
    // Settings/About used to be standalone icon buttons in the topbar; they
    // live in this sidebar footer now (see the redesign that removed the
    // topbar's menu-like buttons), but the dialogs themselves are still
    // owned and rendered by App.svelte, so this just reports the click.
    onSettingsOpen?: () => void
    onAboutOpen?: () => void
    // Ctrl+K on a collapsed sidebar: the shell expands it so the filter can
    // take the focus.
    onRequestExpand?: () => void
  } = $props()

  // "Opened 5 minutes ago" would go stale on screen, so re-read the clock
  // once a minute.
  let now: number = $state(Date.now())
  onMount(() => {
    const timer = setInterval(() => (now = Date.now()), 60_000)
    return () => clearInterval(timer)
  })

  function openedLabel(path: string): string | null {
    const recent = recentFiles.find((f) => f.path === path)
    return recent ? `Opened ${formatRelativeTime(recent.openedAt, now)}` : null
  }

  function canOpen(row: FileRow): boolean {
    return row.defaultChannel !== null && row.status !== 'error'
  }

  function handleRowKeydown(event: KeyboardEvent, row: FileRow) {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      handleReview(row)
    }
  }

  function handleRemove(event: MouseEvent, row: FileRow) {
    // The X sits inside the clickable row; removing must not also open it.
    event.stopPropagation()
    removeFile(row.path)
    onFileRemoved?.(row.path)
  }

  let asideEl: HTMLElement | undefined = $state()
  // Collapsed by default — this section is a secondary, quick-recall
  // affordance (reopening something without re-importing), not the
  // primary file list, so it starts out of the way.
  let recentSectionOpen: boolean = $state(false)

  // Files already in this session's registry are omitted here — they're
  // already visible (and openable) in the main list above, so listing them
  // again under "Recent files" would just be a duplicate entry. The filter's
  // words apply here too (a status does not: these aren't loaded).
  let recentOnly = $derived(
    recentFiles.filter(
      (f) =>
        !fileRegistry.some((row) => row.path === f.path) && matchesQuery(sidebarFilter.query, f.filename, f.path),
    ),
  )

  function handleReview(row: FileRow) {
    if (!canOpen(row) || row.defaultChannel === null) return
    onReview?.({ path: row.path, channels: row.channels, defaultChannel: row.defaultChannel })
  }

  // FP/FN review projects open this session. Their recordings are ordinary
  // registry rows, but they are listed inside their project's tree, so the
  // plain "Files" list leaves them out.
  let projects = $derived(openFlagProjects())
  let projectPaths = $derived(openProjectPaths())
  let fileRows = $derived(fileRegistry.filter((row) => !projectPaths.has(row.path)))
  let hasAnything = $derived(fileRegistry.length > 0 || projects.length > 0)

  // ---- Filter -------------------------------------------------------------
  // Text (file name or path; for FP/FN reviews also the project and animal
  // IDs) and a status group. Both narrow the files and the project trees.
  const STATUS_GROUPS: Record<Exclude<StatusFilter, 'all'>, FileRow['status'][]> = {
    ready: ['ready', 'detected'],
    progress: ['queued', 'detecting'],
    problems: ['error', 'detection-error'],
  }
  const STATUS_FILTER_LABEL: Record<StatusFilter, string> = {
    all: 'All files',
    ready: 'Ready to review',
    progress: 'In progress',
    problems: 'Problems',
  }

  let filtering = $derived(sidebarFilter.query.trim() !== '' || sidebarFilter.status !== 'all')
  let rowsByPath = $derived(new Map(fileRegistry.map((row) => [row.path, row])))

  function statusMatches(status: FileRow['status'] | undefined): boolean {
    if (sidebarFilter.status === 'all') return true
    return status !== undefined && STATUS_GROUPS[sidebarFilter.status].includes(status)
  }

  function rowMatches(row: FileRow): boolean {
    return statusMatches(row.status) && matchesQuery(sidebarFilter.query, row.filename, row.path)
  }

  // A recording of a project shows when its status fits and the words are
  // found in the project's name or in the recording itself (so a project's
  // name shows all its recordings, an animal ID just that one).
  function animalMatches(project: FlagProject, animal: FlagProjectAnimal): boolean {
    const row = rowsByPath.get(animal.filePath ?? '')
    if (!statusMatches(row?.status)) return false
    return matchesQuery(
      sidebarFilter.query,
      project.name,
      project.csvPath,
      animal.animalId,
      row?.filename,
      animal.filePath,
    )
  }

  let visibleFileRows = $derived(filtering ? fileRows.filter(rowMatches) : fileRows)
  let visibleProjects = $derived(
    filtering
      ? projects.filter(
          (project) =>
            projectRecordings(project).some((animal) => animalMatches(project, animal)) ||
            // A project with no recordings found yet still answers to its name.
            (sidebarFilter.status === 'all' && matchesQuery(sidebarFilter.query, project.name, project.csvPath)),
        )
      : projects,
  )
  let nothingMatches = $derived(filtering && hasAnything && visibleFileRows.length === 0 && visibleProjects.length === 0)

  // The "Files" label heads the list unless only projects are shown, in which
  // case "FP/FN reviews" takes the top spot (and carries the Clear button).
  let filesFirst = $derived(visibleFileRows.length > 0 || visibleProjects.length === 0)

  function clearFilter() {
    sidebarFilter.query = ''
    sidebarFilter.status = 'all'
  }

  let statusItems: MenuItem[] = $derived(
    (Object.keys(STATUS_FILTER_LABEL) as StatusFilter[]).map((status) => ({
      id: status,
      label: STATUS_FILTER_LABEL[status],
      checked: sidebarFilter.status === status,
      testid: `sidebar-status-filter-${status}`,
      onSelect: () => (sidebarFilter.status = status),
    })),
  )

  let importItems: MenuItem[] = $derived([
    {
      id: 'files',
      label: 'Files…',
      icon: 'file-plus',
      testid: 'sidebar-import-files-button',
      disabled: importQueue.importing,
      onSelect: () => void handleImportFiles(),
    },
    {
      id: 'folder',
      label: 'Folder…',
      icon: 'folder',
      testid: 'sidebar-import-folder-button',
      disabled: importQueue.importing,
      onSelect: () => void handleImportFolder(),
    },
    {
      id: 'flags',
      label: 'FP/FN list…',
      icon: 'target',
      testid: 'sidebar-import-flag-list-button',
      disabled: importQueue.importing,
      onSelect: () => void startFlagListImport(),
    },
  ])

  let searchInput: HTMLInputElement | undefined = $state()

  // Ctrl+K (Cmd+K) jumps to the filter from anywhere, unless a dialog owns
  // the keyboard.
  async function focusFilter() {
    if (collapsed) {
      onRequestExpand?.()
      await tick()
    }
    searchInput?.focus()
    searchInput?.select()
  }

  onMount(() => {
    function onKeydown(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return
      if (event.key.toLowerCase() !== 'k') return
      if (document.querySelector('[role="dialog"]')) return
      event.preventDefault()
      void focusFilter()
    }
    window.addEventListener('keydown', onKeydown)
    return () => window.removeEventListener('keydown', onKeydown)
  })

  function handleClearFiles() {
    // Clear means everything: closing the projects also takes their
    // recordings out of the registry; the rest goes with the registry itself.
    for (const project of [...projects]) closeFlagProject(project.id)
    clearFileRegistry()
    onFilesCleared?.()
  }

  function toggleRecentSection() {
    recentSectionOpen = !recentSectionOpen
  }

  // Opening another file mid-import would race the one still landing (and a
  // double-click could register it twice), so recent files wait for it.
  function handleRecent(path: string) {
    if (importQueue.importing) return
    void openRecentFile(path)
  }

  // `openRecentFile` (see importQueue.svelte.ts) sets the shared
  // `autoOpenRequest` rather than calling back into this component
  // directly — App.svelte owns navigation and is the sole consumer of that
  // request (see its own effect); this component doesn't need `onReview`
  // for recent-file clicks at all, only for the main file list's Open
  // button below.

  // Drag-to-resize: a thin handle on the sidebar's right edge. Reads the
  // starting width straight off the live element (rather than needing a
  // `width` prop threaded down) so this stays a self-contained interaction —
  // `onResize` just reports the running width, and the caller (App.svelte,
  // which owns the persisted width) clamps/applies it.
  function startResize(event: MouseEvent) {
    if (!onResize) return
    event.preventDefault()
    const startX = event.clientX
    const startWidth = asideEl?.getBoundingClientRect().width ?? 240

    function onMove(moveEvent: MouseEvent) {
      onResize?.(startWidth + (moveEvent.clientX - startX))
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }
</script>

<aside class="sidebar" class:collapsed data-testid="sidebar" bind:this={asideEl}>
  <div class="sidebar-import">
    <!-- One Import menu (files, a folder, an FP/FN list) instead of three
         stacked buttons, leaving the room for the filter below. -->
    <MenuButton
      items={importItems}
      label={importQueue.importing ? 'Import (wait for the current import to finish)' : 'Import'}
      testid="sidebar-import-menu-button"
      triggerClass="btn btn-sm sidebar-import-btn"
      placement={collapsed ? 'right' : 'below'}
    >
      {#snippet trigger()}
        <Icon name="plus" size={16} />
        {#if !collapsed}<span>Import</span><Icon name="chevron-down" size={14} />{/if}
      {/snippet}
    </MenuButton>

    {#if !collapsed}
      <div class="sidebar-filter">
        <SearchField
          bind:value={sidebarFilter.query}
          bind:input={searchInput}
          placeholder="Filter files…  Ctrl+K"
          label="Filter files"
          testid="sidebar-filter-input"
          size="sm"
        />
        <span class="sidebar-filter-status" class:active={sidebarFilter.status !== 'all'}>
          <MenuButton
            items={statusItems}
            label={`Show: ${STATUS_FILTER_LABEL[sidebarFilter.status]}`}
            testid="sidebar-status-filter-button"
            triggerClass="icon-btn sidebar-filter-btn"
          >
            {#snippet trigger()}
              <Icon name="filter" size={14} />
            {/snippet}
          </MenuButton>
        </span>
      </div>
    {:else}
      <!-- Highlighted while a filter narrows the rail below it. -->
      <button
        type="button"
        class="btn btn-sm sidebar-import-btn"
        class:sidebar-filter-on={filtering}
        data-testid="sidebar-search-collapsed-button"
        title={filtering ? 'Files are filtered — click to change (Ctrl+K)' : 'Filter files (Ctrl+K)'}
        aria-label={filtering ? 'Files are filtered; change the filter' : 'Filter files'}
        onclick={() => void focusFilter()}
      >
        <Icon name="search" size={16} />
      </button>
    {/if}
  </div>

  {#if importQueue.detecting && !collapsed}
    <div class="sidebar-detecting" data-testid="sidebar-detecting">
      <span class="text-muted">Detecting beats…</span>
      <button type="button" class="btn btn-sm" data-testid="sidebar-stop-button" onclick={handleStop}>Stop</button>
    </div>
  {/if}

  {#if !collapsed}
    <div class="sidebar-list-header">
      <h3 class="sidebar-list-label" data-testid="sidebar-list-label">
        {filesFirst ? 'Files' : 'FP/FN reviews'}
        {#if filtering && filesFirst && fileRows.length > 0}
          <span class="sidebar-list-count">· {visibleFileRows.length} of {fileRows.length}</span>
        {/if}
      </h3>
      {#if hasAnything}
        <button
          type="button"
          class="btn btn-sm sidebar-clear-button"
          data-testid="sidebar-clear-files-button"
          title="Clear the file list and close FP/FN reviews (does not delete anything or discard saved review work)"
          disabled={importQueue.importing || importQueue.detecting}
          onclick={handleClearFiles}
        >
          <Icon name="trash" size={12} /> Clear
        </button>
      {/if}
    </div>
  {/if}
  <div class="sidebar-list" data-testid="sidebar-file-list">
    {#each visibleFileRows as row (row.path)}
      <!-- The whole row opens the file: a click anywhere on it, or Enter/Space
           when focused. The X inside it is its own button. -->
      <div
        class="sidebar-row"
        class:active={row.path === activePath}
        class:clickable={canOpen(row)}
        data-testid="file-row"
        title={row.filename}
        role="button"
        tabindex={canOpen(row) ? 0 : -1}
        aria-disabled={!canOpen(row)}
        onclick={() => handleReview(row)}
        onkeydown={(event) => handleRowKeydown(event, row)}
      >
        <SidebarStatusDot status={row.status} />
        {#if !collapsed}
          <span class="sidebar-row-body">
            <span class="sidebar-row-name">{row.filename}</span>
            <SidebarRowStatus {row} />
            {#if openedLabel(row.path)}
              <span class="sidebar-row-opened text-muted" data-testid="file-row-opened">{openedLabel(row.path)}</span>
            {/if}
          </span>
          <button
            type="button"
            class="icon-btn sidebar-row-remove"
            data-testid="remove-file-button"
            title="Remove from this list (saved review work is kept)"
            aria-label={`Remove ${row.filename} from the list`}
            onclick={(event) => handleRemove(event, row)}
          >
            <Icon name="x" size={14} />
          </button>
        {/if}
      </div>
    {/each}
    {#if visibleProjects.length > 0}
      {#if filesFirst && !collapsed}
        <h3 class="sidebar-list-label sidebar-group-label">FP/FN reviews</h3>
      {/if}
      <SidebarProjectTree
        projects={visibleProjects}
        {collapsed}
        {activePath}
        {onFileRemoved}
        animalFilter={filtering ? animalMatches : null}
      />
    {/if}
    {#if !hasAnything && !collapsed}
      <p class="sidebar-empty text-muted">No files imported yet</p>
    {/if}
    {#if nothingMatches && !collapsed}
      <div class="sidebar-empty text-muted" data-testid="sidebar-no-matches">
        <p>
          {sidebarFilter.query.trim()
            ? `No files match “${sidebarFilter.query.trim()}”`
            : `No files are ${STATUS_FILTER_LABEL[sidebarFilter.status].toLowerCase()}`}
        </p>
        <button type="button" class="btn btn-sm" data-testid="sidebar-clear-filter-button" onclick={clearFilter}>
          Clear filter
        </button>
      </div>
    {/if}
  </div>

  {#if !collapsed}
    <div class="sidebar-section">
      <button
        type="button"
        class="sidebar-section-toggle"
        data-testid="recent-files-toggle"
        onclick={toggleRecentSection}
        aria-expanded={recentSectionOpen}
      >
        <Icon name={recentSectionOpen ? 'chevron-down' : 'chevron-right'} size={14} />
        <span>Recent files</span>
      </button>
      {#if recentSectionOpen}
        <div class="sidebar-recent-list" data-testid="sidebar-recent-list">
          {#if recentOnly.length === 0}
            <p class="sidebar-empty text-muted">
              {sidebarFilter.query.trim() ? 'No recent files match' : 'No recent files'}
            </p>
          {:else}
            {#each recentOnly as file (file.path)}
              <button
                type="button"
                class="sidebar-recent-row"
                data-testid="sidebar-recent-file"
                title={importQueue.importing ? 'Wait for the import to finish' : file.filename}
                disabled={importQueue.importing}
                onclick={() => handleRecent(file.path)}
              >
                <Icon name="file-text" size={14} />
                <span class="sidebar-recent-name">{file.filename}</span>
              </button>
            {/each}
          {/if}
        </div>
      {/if}
    </div>
  {/if}

  <div class="sidebar-footer">
    <button
      type="button"
      class="sidebar-footer-btn"
      data-testid="settings-button"
      onclick={() => onSettingsOpen?.()}
      title="Settings"
      aria-label="Settings"
    >
      <Icon name="settings" size={16} />
      {#if !collapsed}<span>Settings</span>{/if}
    </button>
    <button
      type="button"
      class="sidebar-footer-btn"
      data-testid="about-button"
      onclick={() => onAboutOpen?.()}
      title="About"
      aria-label="About"
    >
      <Icon name="info" size={16} />
      {#if !collapsed}<span>About</span>{/if}
    </button>
  </div>

  {#if !collapsed}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div
      class="sidebar-resize-handle"
      data-testid="sidebar-resize-handle"
      onmousedown={startResize}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
    ></div>
  {/if}
</aside>

<style>
  .sidebar {
    grid-area: sidebar;
    position: relative;
    display: flex;
    flex-direction: column;
    background: var(--color-surface);
    border-right: 1px solid var(--color-border);
    overflow: hidden;
  }

  .sidebar-resize-handle {
    position: absolute;
    top: 0;
    right: -3px;
    width: 6px;
    height: 100%;
    cursor: col-resize;
    z-index: 10;
  }

  .sidebar-resize-handle:hover,
  .sidebar-resize-handle:active {
    background: var(--color-accent-soft);
  }

  .sidebar-detecting {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--color-border);
    font-size: var(--font-size-sm);
  }

  .sidebar-import {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-2);
    padding: var(--space-3);
    border-bottom: 1px solid var(--color-border);
  }

  .sidebar.collapsed .sidebar-import {
    align-items: center;
  }

  .sidebar-import :global(.sidebar-import-btn) {
    justify-content: center;
  }

  .sidebar.collapsed .sidebar-import :global(.sidebar-import-btn) {
    width: 32px;
    height: 32px;
    padding: 0;
  }

  .sidebar-filter {
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }

  .sidebar-filter-status {
    position: relative;
    display: inline-flex;
  }

  .sidebar-filter-status :global(.sidebar-filter-btn) {
    width: 30px;
    height: 30px;
  }

  /* A status filter is on: mark the button so a short list isn't a mystery. */
  .sidebar-filter-status.active :global(.sidebar-filter-btn) {
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }

  .sidebar-filter-status.active::after {
    content: '';
    position: absolute;
    top: 3px;
    right: 3px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--color-accent);
    pointer-events: none;
  }

  .sidebar-import .sidebar-filter-on {
    border-color: var(--color-accent);
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }

  .sidebar-list-count {
    font-weight: 500;
    text-transform: none;
    letter-spacing: 0;
  }

  .sidebar-list-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding-right: var(--space-2);
  }

  .sidebar-list-label {
    padding: var(--space-2) var(--space-3) 0;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-muted);
  }

  /* The "FP/FN reviews" label inside the scrolling list, under the files. */
  .sidebar-group-label {
    padding: var(--space-2) var(--space-1) 0;
  }

  .sidebar-clear-button {
    margin-top: var(--space-2);
    font-size: 0.7rem;
  }

  .sidebar-list {
    flex: 1;
    overflow-y: auto;
    padding: var(--space-2);
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .sidebar-empty {
    padding: var(--space-3);
    font-size: var(--font-size-sm);
    text-align: center;
  }

  .sidebar-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2);
    border-radius: var(--radius-sm);
    border-left: 3px solid transparent;
    min-width: 0;
    transition: background var(--transition-fast);
  }

  .sidebar-row:hover {
    background: var(--color-surface-hover);
  }

  .sidebar-row.active {
    background: var(--color-accent-soft);
    border-left-color: var(--color-accent);
  }

  .sidebar-row.clickable {
    cursor: pointer;
  }

  .sidebar.collapsed .sidebar-row {
    justify-content: center;
    padding: var(--space-2) 0;
  }

  .sidebar-row-body {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .sidebar-row-name {
    font-size: var(--font-size-sm);
    font-weight: 500;
    color: var(--color-text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sidebar-row-opened {
    font-size: 0.7rem;
  }

  .sidebar-row-remove {
    flex-shrink: 0;
    opacity: 0;
    transition: opacity var(--transition-fast);
  }

  /* Shown on hover and whenever the row or the X has keyboard focus. */
  .sidebar-row:hover .sidebar-row-remove,
  .sidebar-row:focus-within .sidebar-row-remove {
    opacity: 1;
  }

  .sidebar-section {
    border-top: 1px solid var(--color-border);
  }

  .sidebar-section-toggle {
    width: 100%;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3);
    background: none;
    border: none;
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    font-weight: 600;
  }

  .sidebar-section-toggle:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .sidebar-recent-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: 0 var(--space-2) var(--space-2);
    max-height: 200px;
    overflow-y: auto;
  }

  .sidebar-recent-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2);
    border-radius: var(--radius-sm);
    background: none;
    border: none;
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    text-align: left;
  }

  .sidebar-recent-row:hover:not(:disabled) {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .sidebar-recent-row:disabled {
    opacity: 0.5;
  }

  .sidebar-recent-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sidebar-footer {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-2);
    border-top: 1px solid var(--color-border);
  }

  .sidebar.collapsed .sidebar-footer {
    align-items: center;
  }

  .sidebar-footer-btn {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding: var(--space-2) var(--space-3);
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    text-align: left;
    transition: background var(--transition-fast), color var(--transition-fast);
  }

  .sidebar-footer-btn:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .sidebar.collapsed .sidebar-footer-btn {
    width: 32px;
    height: 32px;
    padding: 0;
    justify-content: center;
  }
</style>
