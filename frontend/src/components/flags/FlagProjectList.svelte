<script lang="ts">
  import { formatRelativeTime } from '../../lib/format'
  import { importQueue } from '../../lib/stores/importQueue.svelte'
  import {
    editFlagProject,
    flagProjects,
    flagSession,
    openFlagProject,
    projectStats,
    removeFlagProjects,
  } from '../../lib/stores/flagProjects.svelte'
  import Icon from '../shared/Icon.svelte'
  import FlagCountPill from './FlagCountPill.svelte'

  // The rows of Home's "FP/FN reviews" section. `now` is the Home screen's
  // minute ticker, so "Opened 5 minutes ago" stays honest while it's open.
  let { now }: { now: number } = $props()

  function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
    return count === 1 ? singular : pluralForm
  }
</script>

<ul class="flag-project-list">
  {#each flagProjects as project (project.id)}
    {@const stats = projectStats(project)}
    <li class="flag-project" data-testid="flag-project-row">
      <span class="flag-project-icon"><Icon name="target" size={18} /></span>

      <div class="flag-project-main">
        <div class="flag-project-heading">
          <span class="flag-project-name" title={project.csvPath}>{project.name}</span>
          {#if flagSession.openIds.includes(project.id)}
            <span class="flag-project-status" data-testid="flag-project-open-status">In sidebar</span>
          {/if}
        </div>

        <div class="flag-project-meta text-muted">
          <span data-testid="flag-project-found">
            {stats.found} of {stats.selected}
            {plural(stats.selected, 'recording')} found · {stats.flags} flagged {plural(stats.flags, 'beat')}
          </span>
          {#if stats.fp > 0}<FlagCountPill kind="fp" count={stats.fp} />{/if}
          {#if stats.fn > 0}<FlagCountPill kind="fn" count={stats.fn} />{/if}
          {#if stats.selected < stats.animals}
            <span>· {stats.selected} of {stats.animals} animals</span>
          {/if}
          <span data-testid="flag-project-opened">· Opened {formatRelativeTime(project.openedAt, now)}</span>
        </div>

        {#if stats.found === 0}
          <p class="flag-project-hint text-muted">No recordings found yet — use Find recordings…</p>
        {/if}
      </div>

      <div class="flag-project-actions">
        <button
          type="button"
          class="btn btn-sm btn-primary"
          data-testid="flag-project-open-button"
          disabled={stats.found === 0 || importQueue.importing}
          onclick={() => void openFlagProject(project.id)}
        >
          Open
        </button>
        <button
          type="button"
          class="btn btn-sm"
          data-testid="flag-project-edit-button"
          onclick={() => editFlagProject(project.id)}
        >
          <Icon name="search" size={14} /> Find recordings…
        </button>
        <button
          type="button"
          class="icon-btn"
          data-testid="flag-project-remove-button"
          aria-label={`Remove ${project.name} from the list`}
          title="Remove from this list"
          onclick={() => removeFlagProjects([project.id])}
        >
          <Icon name="x" size={16} />
        </button>
      </div>
    </li>
  {/each}
</ul>

<style>
  .flag-project-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin: 0;
    padding: 0;
    list-style: none;
    container-type: inline-size;
  }

  .flag-project {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3);
    border-radius: var(--radius-md);
    border: 1px solid var(--color-border);
    background: var(--color-surface);
  }

  .flag-project-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 36px;
    height: 36px;
    border-radius: var(--radius-md);
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }

  .flag-project-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .flag-project-heading {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
  }

  .flag-project-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
    font-size: var(--font-size-sm);
    color: var(--color-text);
  }

  .flag-project-status {
    flex-shrink: 0;
    padding: 1px var(--space-2);
    border-radius: 999px;
    background: var(--color-accent-soft);
    color: var(--color-accent);
    font-size: 0.75rem;
    font-weight: 600;
    line-height: 1.5;
  }

  .flag-project-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1) var(--space-2);
    font-size: 0.75rem;
  }

  .flag-project-hint {
    font-size: 0.75rem;
  }

  .flag-project-actions {
    display: flex;
    align-items: center;
    flex-shrink: 0;
    gap: var(--space-2);
  }

  /* Narrow list (small window, or sidebar open): actions drop under the text
     instead of squeezing it. */
  @container (max-width: 620px) {
    .flag-project {
      flex-wrap: wrap;
    }

    .flag-project-actions {
      width: 100%;
      justify-content: flex-end;
    }
  }
</style>
