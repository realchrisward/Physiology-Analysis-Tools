<script lang="ts">
  import { fileRegistry, type FileRow } from '../../lib/stores/fileRegistry.svelte'
  import { formatEta, handleImportFiles, handleImportFolder, handleStop, importQueue } from '../../lib/stores/importQueue.svelte'
  import Icon from '../shared/Icon.svelte'

  interface ReviewSelection {
    path: string
    channels: string[]
    defaultChannel: string
  }

  let {
    collapsed = false,
    activePath = null,
    onReview,
    onResize,
  }: {
    collapsed?: boolean
    activePath?: string | null
    onReview?: (selection: ReviewSelection) => void
    onResize?: (width: number) => void
  } = $props()

  let asideEl: HTMLElement | undefined = $state()

  function handleReview(row: FileRow) {
    if (row.defaultChannel === null) return
    onReview?.({ path: row.path, channels: row.channels, defaultChannel: row.defaultChannel })
  }

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
    <button
      type="button"
      class="btn btn-sm sidebar-import-btn"
      data-testid="sidebar-import-files-button"
      onclick={handleImportFiles}
      disabled={importQueue.importing}
      title="Import files"
      aria-label="Import files"
    >
      <Icon name="file-plus" size={16} />
      {#if !collapsed}<span>Import</span>{/if}
    </button>
    {#if !collapsed}
      <button
        type="button"
        class="icon-btn"
        data-testid="sidebar-import-folder-button"
        onclick={handleImportFolder}
        disabled={importQueue.importing}
        title="Import folder"
        aria-label="Import folder"
      >
        <Icon name="folder" size={16} />
      </button>
    {/if}
  </div>

  {#if importQueue.detecting && !collapsed}
    <div class="sidebar-detecting" data-testid="sidebar-detecting">
      <span class="text-muted">Detecting beats…</span>
      <button type="button" class="btn btn-sm" data-testid="sidebar-stop-button" onclick={handleStop}>Stop</button>
    </div>
  {/if}

  <div class="sidebar-list" data-testid="sidebar-file-list">
    {#each fileRegistry as row (row.path)}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <div
        class="sidebar-row"
        class:active={row.path === activePath}
        class:clickable={collapsed && row.defaultChannel !== null && row.status !== 'error'}
        data-testid="file-row"
        title={row.filename}
        onclick={collapsed ? () => handleReview(row) : undefined}
      >
        <span class="sidebar-row-dot" data-status={row.status}></span>
        {#if !collapsed}
          <span class="sidebar-row-body">
            <span class="sidebar-row-name">{row.filename}</span>
            {#if row.status === 'ready'}
              <span class="sidebar-row-status text-muted">ready</span>
            {:else if row.status === 'detecting'}
              <span class="sidebar-row-status text-muted"
                >detecting… <span data-testid="eta-badge">{formatEta(row.size)}</span></span
              >
            {:else if row.status === 'detected'}
              <span class="sidebar-row-status text-muted"
                >{row.beatCount} beats detected{row.meanHr !== null ? `, mean HR ${row.meanHr}` : ''}</span
              >
            {:else if row.status === 'detection-error'}
              <span class="sidebar-row-status text-danger">detection failed: {row.error}</span>
            {:else}
              <span class="sidebar-row-status text-danger">{row.error}</span>
            {/if}
          </span>
          {#if row.defaultChannel !== null && row.status !== 'error'}
            <button
              type="button"
              class="btn btn-sm sidebar-row-review"
              data-testid="review-button"
              onclick={() => handleReview(row)}
            >
              Review
            </button>
          {/if}
        {/if}
      </div>
    {/each}
    {#if fileRegistry.length === 0 && !collapsed}
      <p class="sidebar-empty text-muted">No files imported yet</p>
    {/if}
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
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3);
    border-bottom: 1px solid var(--color-border);
  }

  .sidebar.collapsed .sidebar-import {
    justify-content: center;
  }

  .sidebar-import-btn {
    flex: 1;
    justify-content: center;
  }

  .sidebar.collapsed .sidebar-import-btn {
    flex: none;
    width: 32px;
    height: 32px;
    padding: 0;
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

  .sidebar-row-dot {
    flex-shrink: 0;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-text-muted);
  }

  .sidebar-row-dot[data-status='ready'] {
    background: var(--color-text-muted);
  }

  .sidebar-row-dot[data-status='detecting'] {
    background: var(--color-accent);
    animation: pulse 1.4s ease-in-out infinite;
  }

  .sidebar-row-dot[data-status='detected'] {
    background: var(--color-success);
  }

  .sidebar-row-dot[data-status='detection-error'],
  .sidebar-row-dot[data-status='error'] {
    background: var(--color-danger);
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.35;
    }
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

  .sidebar-row-status {
    font-size: 0.75rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sidebar-row-review {
    flex-shrink: 0;
  }
</style>
