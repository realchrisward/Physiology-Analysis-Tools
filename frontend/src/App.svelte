<script lang="ts">
  import { untrack } from 'svelte'
  import ImportScreen from './components/import/ImportScreen.svelte'
  import ReviewWorkspace from './components/review/ReviewWorkspace.svelte'
  import UncertainLeaveDialog from './components/review/UncertainLeaveDialog.svelte'
  import SettingsDialog from './components/settings/SettingsDialog.svelte'
  import Sidebar from './components/shell/Sidebar.svelte'
  import AboutDialog from './components/shell/AboutDialog.svelte'
  import Icon from './components/shared/Icon.svelte'
  import FlagListImportDialog from './components/flags/FlagListImportDialog.svelte'
  import { themeState } from './lib/stores/theme.svelte'
  import { autoOpenRequest, fileRegistry, reviewNavigation, type ReviewSelection } from './lib/stores/fileRegistry.svelte'
  import { recordRecentFile } from './lib/stores/recentFiles.svelte'
  import { closeFlagImportDialog, flagImportDialog, flagSession, getFlagProject } from './lib/stores/flagProjects.svelte'
  import {
    answerLeave,
    clearPendingUncertain,
    confirmLeave,
    hasPendingUncertain,
    reviewGuard,
  } from './lib/stores/reviewGuard.svelte'

  const SIDEBAR_WIDTH_KEY = 'pat.sidebarWidth'
  const MIN_SIDEBAR_WIDTH = 180
  const MAX_SIDEBAR_WIDTH = 420
  const DEFAULT_SIDEBAR_WIDTH = 240

  function loadSidebarWidth(): number {
    try {
      const raw = localStorage.getItem(SIDEBAR_WIDTH_KEY)
      const parsed = raw ? Number(raw) : NaN
      if (!Number.isNaN(parsed) && parsed >= MIN_SIDEBAR_WIDTH && parsed <= MAX_SIDEBAR_WIDTH) return parsed
    } catch {
      // ignore
    }
    return DEFAULT_SIDEBAR_WIDTH
  }

  let view: 'import' | 'review' = $state('import')
  let selectedFile: ReviewSelection | null = $state(null)
  // What ReviewWorkspace is rendered from. Unlike `selectedFile` it is never
  // cleared: a workspace that is unmounting can still have requests in
  // flight that read its props, and a prop getter onto a now-null
  // `selectedFile` would throw.
  let shownFile: ReviewSelection | null = $state(null)
  let settingsOpen: boolean = $state(false)
  let aboutOpen: boolean = $state(false)
  let sidebarCollapsed: boolean = $state(false)
  let sidebarWidth: number = $state(loadSidebarWidth())

  // Opening a different file leaves the open beat, so a beat marked "uncertain"
  // but not yet decided has to be dealt with first (see reviewGuard.svelte.ts).
  // Opened at once when nothing is pending, so ordinary opening keeps its timing.
  function handleReview(row: ReviewSelection) {
    if (hasPendingUncertain() && row.path !== selectedFile?.path) {
      void confirmLeave().then((leave) => {
        if (leave) openReview(row)
      })
      return
    }
    openReview(row)
  }

  function openReview(row: ReviewSelection) {
    selectedFile = row
    shownFile = row
    reviewNavigation.seq += 1
    view = 'review'
    // Every way of opening a file lands here, so this is the one place that
    // keeps its "last opened" time current. FP/FN project recordings stay out
    // of Recent files — the project itself is the thing to reopen.
    if (!row.projectId) recordRecentFile(row.path, fileNameOf(row.path))
  }

  // Only while that project is still open: a recording another open project
  // also uses stays on screen when the first one closes, and must not keep
  // naming it.
  function projectOf(selection: ReviewSelection | null) {
    const id = selection?.projectId
    return id && flagSession.openIds.includes(id) ? getFlagProject(id) : undefined
  }

  let selectedProject = $derived(projectOf(selectedFile))

  function handleFileRemoved(path: string) {
    if (selectedFile?.path === path) handleFilesCleared()
  }

  function handleBack() {
    if (hasPendingUncertain()) {
      void confirmLeave().then((leave) => {
        if (leave) view = 'import'
      })
      return
    }
    view = 'import'
  }

  // Clearing the file list while a file is open would leave the review
  // workspace showing a file that is no longer listed anywhere, so fall back
  // to the welcome screen. Removing files is an explicit act, so it is not
  // questioned: an unsaved "uncertain" mark simply goes with the file.
  function handleFilesCleared() {
    clearPendingUncertain()
    selectedFile = null
    view = 'import'
  }

  // The sidebar reports its own removals (above), but FP/FN reviews can also
  // drop recordings from Home or the import dialog. Whatever the route, a
  // file that is no longer in the session can't stay open.
  $effect(() => {
    const path = selectedFile?.path
    if (path && !fileRegistry.some((row) => row.path === path)) handleFilesCleared()
  })

  function toggleSidebar() {
    sidebarCollapsed = !sidebarCollapsed
  }

  function handleSidebarResize(width: number) {
    sidebarWidth = Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, Math.round(width)))
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth))
    } catch {
      // ignore
    }
  }

  function fileNameOf(path: string): string {
    return path.split(/[\\/]/).pop() ?? path
  }

  // Reflects the appearance preference onto <html data-theme="...">, which
  // tokens.css keys its light/dark/bw palettes off. 'system' removes the
  // attribute entirely so the existing prefers-color-scheme media query
  // takes over, matching this store's own contract.
  $effect(() => {
    const mode = themeState.mode
    if (mode === 'system') {
      document.documentElement.removeAttribute('data-theme')
    } else {
      document.documentElement.setAttribute('data-theme', mode)
    }
  })

  // Opens a file automatically the moment it's ready, set by
  // importQueue.svelte.ts after an explicit import action (Import Files/
  // Folder, or opening a Recent file) — see `autoOpenRequest`'s own
  // comment. Consumed and cleared immediately so it only fires once per
  // request, not on every subsequent reactive run this effect happens to see.
  $effect(() => {
    const pending = autoOpenRequest.value
    if (pending) {
      // Untracked: opening reads (and, once answered, writes) state this effect
      // has no business re-running for.
      untrack(() => handleReview(pending))
      autoOpenRequest.value = null
    }
  })
</script>

<div
  class="app-shell"
  class:sidebar-collapsed={sidebarCollapsed}
  style={`--sidebar-width: ${sidebarWidth}px`}
>
  <header class="topbar">
    <button
      type="button"
      class="icon-btn"
      data-testid="sidebar-toggle-button"
      onclick={toggleSidebar}
      aria-label="Toggle sidebar"
      aria-pressed={sidebarCollapsed}
    >
      <Icon name="menu" />
    </button>
    <span class="app-name">Physiology Analysis Tools</span>

    {#if view === 'review' && selectedFile}
      <nav class="breadcrumb">
        <button type="button" class="breadcrumb-back" data-testid="back-to-import-button" onclick={handleBack}>
          <Icon name="chevron-left" size={16} />
          Home
        </button>
        <span class="breadcrumb-sep">/</span>
        {#if selectedProject}
          <span class="breadcrumb-project" data-testid="breadcrumb-project" title={selectedProject.csvPath}>
            <Icon name="target" size={14} />
            {selectedProject.name}
          </span>
          <span class="breadcrumb-sep">/</span>
        {/if}
        <span class="breadcrumb-current" data-testid="breadcrumb-current">{fileNameOf(selectedFile.path)}</span>
      </nav>
    {/if}
  </header>

  <Sidebar
    collapsed={sidebarCollapsed}
    activePath={selectedFile?.path ?? null}
    onReview={handleReview}
    onResize={handleSidebarResize}
    onFilesCleared={handleFilesCleared}
    onFileRemoved={handleFileRemoved}
    onSettingsOpen={() => (settingsOpen = true)}
    onAboutOpen={() => (aboutOpen = true)}
  />

  <main class="main-content">
    {#if view === 'import'}
      <ImportScreen />
    {:else if view === 'review' && selectedFile && shownFile}
      <!-- Keyed on path: the sidebar is reachable (and its files clickable)
           from inside Review now, not just the Welcome screen, so switching
           directly from one open file to another no longer unmounts this
           `{#if}` block first (view stays 'review' the whole time) — without
           this key, ReviewWorkspace would keep its OLD internal state
           (activeChannel, hydrationChecked, the mounted EcgGraph, etc.)
           while only its props silently changed underneath it, since most
           of that state is deliberately seeded once from props on mount,
           not kept in sync with them afterward (see its own comments on
           `initialChannel`). Forcing a full remount on path change is the
           same "start clean" approach already used for `{#key
           activeChannel}` inside EcgGraph. -->
      {#key shownFile.path}
        <ReviewWorkspace
          path={shownFile.path}
          channels={shownFile.channels}
          defaultChannel={shownFile.defaultChannel}
        />
      {/key}
    {/if}
  </main>

  {#if settingsOpen}
    <SettingsDialog onClose={() => (settingsOpen = false)} />
  {/if}

  {#if aboutOpen}
    <AboutDialog onClose={() => (aboutOpen = false)} />
  {/if}

  {#if flagImportDialog.open}
    <!-- Keyed so a different list or project always starts the dialog over:
         it reads its props once, on mount. -->
    {#key `${flagImportDialog.csvPath}|${flagImportDialog.projectId}`}
      <FlagListImportDialog
        csvPath={flagImportDialog.csvPath}
        projectId={flagImportDialog.projectId}
        onClose={closeFlagImportDialog}
      />
    {/key}
  {/if}

  {#if reviewGuard.promptOpen}
    <UncertainLeaveDialog onStay={() => answerLeave(false)} onLeave={() => answerLeave(true)} />
  {/if}
</div>
