<script lang="ts">
  import type { FileRow } from '../../lib/stores/fileRegistry.svelte'

  // The small status dot at the left of a sidebar row. Shared by the plain
  // file rows (Sidebar.svelte) and a review project's recording rows
  // (SidebarProjectTree.svelte) so a file looks the same wherever it is listed.
  let { status }: { status: FileRow['status'] } = $props()
</script>

<span class="sidebar-row-dot" data-status={status}></span>

<style>
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

  .sidebar-row-dot[data-status='queued'] {
    background: var(--color-warning);
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
</style>
