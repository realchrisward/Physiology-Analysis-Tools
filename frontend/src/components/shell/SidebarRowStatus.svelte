<script lang="ts">
  import type { FileRow } from '../../lib/stores/fileRegistry.svelte'
  import { formatEta } from '../../lib/stores/importQueue.svelte'

  // The status line under a sidebar row's name (ready / queued / detecting
  // with ETA / beats detected / errors). Shared by the plain file rows and a
  // review project's recording rows. `row` is null for a project recording
  // whose file has not reached the registry yet.
  let { row }: { row: FileRow | null } = $props()
</script>

{#if row === null}
  <span class="sidebar-row-status text-muted">waiting to import…</span>
{:else if row.status === 'ready'}
  <span class="sidebar-row-status text-muted">ready</span>
{:else if row.status === 'queued'}
  <span class="sidebar-row-status text-muted">queued for detection…</span>
{:else if row.status === 'detecting'}
  <span class="sidebar-row-status text-muted"
    ><span class="spinner" aria-hidden="true"></span> detecting… <span data-testid="eta-badge"
      >{formatEta(row.size)}</span
    ></span
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

<style>
  .sidebar-row-status {
    font-size: 0.75rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .spinner {
    display: inline-block;
    width: 10px;
    height: 10px;
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
</style>
