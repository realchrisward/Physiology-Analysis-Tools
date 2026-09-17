<script lang="ts">
  import ImportScreen from './components/import/ImportScreen.svelte'
  import ReviewWorkspace from './components/review/ReviewWorkspace.svelte'
  import SettingsDialog from './components/settings/SettingsDialog.svelte'
  import Sidebar from './components/shell/Sidebar.svelte'
  import Icon from './components/shared/Icon.svelte'
  import { themeState } from './lib/stores/theme.svelte'
  import { autoOpenRequest, type ReviewSelection } from './lib/stores/fileRegistry.svelte'

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
  let settingsOpen: boolean = $state(false)
  let sidebarCollapsed: boolean = $state(false)
  let sidebarWidth: number = $state(loadSidebarWidth())

  function handleReview(row: ReviewSelection) {
    selectedFile = row
    view = 'review'
  }

  function handleBack() {
    view = 'import'
  }

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
      handleReview(pending)
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
          Import
        </button>
        <span class="breadcrumb-sep">/</span>
        <span class="breadcrumb-current" data-testid="breadcrumb-current">{fileNameOf(selectedFile.path)}</span>
      </nav>
    {/if}

    <span class="topbar-spacer"></span>

    <button
      type="button"
      class="icon-btn"
      data-testid="settings-button"
      onclick={() => (settingsOpen = true)}
      aria-label="Settings"
    >
      <Icon name="settings" />
    </button>
  </header>

  <Sidebar
    collapsed={sidebarCollapsed}
    activePath={selectedFile?.path ?? null}
    onReview={handleReview}
    onResize={handleSidebarResize}
  />

  <main class="main-content">
    {#if view === 'import'}
      <ImportScreen />
    {:else if view === 'review' && selectedFile}
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
      {#key selectedFile.path}
        <ReviewWorkspace
          path={selectedFile.path}
          channels={selectedFile.channels}
          defaultChannel={selectedFile.defaultChannel}
        />
      {/key}
    {/if}
  </main>

  {#if settingsOpen}
    <SettingsDialog onClose={() => (settingsOpen = false)} />
  {/if}
</div>
