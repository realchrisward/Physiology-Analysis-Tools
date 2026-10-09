<script lang="ts">
  import type { FlagKind } from '../../lib/api/types'
  import {
    FLAG_KIND_COLOR_VAR,
    FLAG_KIND_LABEL,
    FLAG_KIND_SOFT_VAR,
    FLAG_KIND_TITLE,
    formatFlagTime,
    formatPrediction,
  } from '../../lib/flags'
  import { fileRegistry, type FileRow } from '../../lib/stores/fileRegistry.svelte'
  import { importQueue } from '../../lib/stores/importQueue.svelte'
  import {
    closeFlagProject,
    editFlagProject,
    flagFocus,
    openProjectAnimal,
    projectRecordings,
    projectStats,
    type FlagProject,
    type FlagProjectAnimal,
    type FlaggedTime,
  } from '../../lib/stores/flagProjects.svelte'
  import Icon from '../shared/Icon.svelte'
  import SidebarRowStatus from './SidebarRowStatus.svelte'
  import SidebarStatusDot from './SidebarStatusDot.svelte'

  // The "FP/FN reviews" group of the sidebar: one tree per open project —
  // the CSV at the root, its recordings below it, and each recording's flagged
  // timestamps below that. Recordings are ordinary file-registry rows (they go
  // through the same import + detection pipeline), so a recording row looks
  // like a file row with a flag summary added.
  let {
    projects,
    collapsed = false,
    activePath = null,
    onFileRemoved,
  }: {
    projects: FlagProject[]
    collapsed?: boolean
    activePath?: string | null
    // Closing a project takes its recordings out of the file list; the app
    // shell has to leave the review view if one of them was open.
    onFileRemoved?: (path: string) => void
  } = $props()

  // Projects start expanded, so only the ones the user folded are recorded.
  let foldedProjects: Record<string, boolean> = $state({})
  // A recording's flag list follows whether it is the open file (the active
  // recording unfolds itself) until the user toggles it, after which their
  // choice wins. Keyed by project + animal.
  let flagListOverride: Record<string, boolean> = $state({})

  let rowsByPath = $derived(new Map(fileRegistry.map((row) => [row.path, row])))

  function canOpen(row: FileRow | undefined): boolean {
    return row !== undefined && row.defaultChannel !== null && row.status !== 'error'
  }

  function activateOnKey(event: KeyboardEvent, action: () => void) {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      action()
    }
  }

  function toggleProject(project: FlagProject) {
    foldedProjects[project.id] = !foldedProjects[project.id]
  }

  function listKey(project: FlagProject, animal: FlagProjectAnimal): string {
    return `${project.id}\n${animal.animalId}`
  }

  function flagListOpen(project: FlagProject, animal: FlagProjectAnimal): boolean {
    return flagListOverride[listKey(project, animal)] ?? (animal.filePath !== null && animal.filePath === activePath)
  }

  function toggleFlagList(event: MouseEvent, project: FlagProject, animal: FlagProjectAnimal) {
    // The chevron sits inside the clickable recording row; it must not also open it.
    event.stopPropagation()
    flagListOverride[listKey(project, animal)] = !flagListOpen(project, animal)
  }

  function handleClose(event: MouseEvent, project: FlagProject) {
    // The X sits inside the clickable project row; closing must not also fold it.
    event.stopPropagation()
    // Closing mid-import would leave the recordings still landing as loose
    // files (and a late navigation opening a project that is already closed).
    if (importQueue.importing) return
    for (const path of closeFlagProject(project.id)) onFileRemoved?.(path)
  }

  function handleFind(project: FlagProject) {
    if (importQueue.importing) return
    editFlagProject(project.id)
  }

  function openRecording(project: FlagProject, animal: FlagProjectAnimal, row: FileRow | undefined) {
    if (!canOpen(row)) return
    openProjectAnimal(project.id, animal.animalId)
  }

  function openFlag(project: FlagProject, animal: FlagProjectAnimal, row: FileRow | undefined, flag: FlaggedTime) {
    if (!canOpen(row)) return
    openProjectAnimal(project.id, animal.animalId, flag.ts)
  }

  function isActiveFlag(animal: FlagProjectAnimal, flag: FlaggedTime): boolean {
    return animal.filePath !== null && flagFocus.path === animal.filePath && flagFocus.ts === flag.ts
  }

  const KIND_ORDER: FlagKind[] = ['fp', 'fn', 'flag']

  // "3 FP · 1 FN": how many flags of each kind a recording has.
  function kindCounts(animal: FlagProjectAnimal): { kind: FlagKind; count: number }[] {
    const counts: Record<FlagKind, number> = { fp: 0, fn: 0, flag: 0 }
    for (const flag of animal.flags) counts[flag.kind] += 1
    return KIND_ORDER.filter((kind) => counts[kind] > 0).map((kind) => ({ kind, count: counts[kind] }))
  }
</script>

<div class="project-tree" class:collapsed>
  {#each projects as project (project.id)}
    {@const stats = projectStats(project)}
    {@const missing = stats.selected - stats.found}
    {@const expanded = collapsed || !foldedProjects[project.id]}
    <div class="project">
      {#if collapsed}
        <div class="project-root-icon" data-testid="flag-project-node" role="img" aria-label={project.name} title={project.name}>
          <Icon name="target" size={16} />
        </div>
      {:else}
        <!-- The whole row folds/unfolds the project: a click anywhere on it, or
             Enter/Space when focused. The X inside it is its own button. -->
        <div
          class="project-root"
          data-testid="flag-project-node"
          title={project.csvPath}
          role="button"
          tabindex="0"
          aria-expanded={expanded}
          onclick={() => toggleProject(project)}
          onkeydown={(event) => activateOnKey(event, () => toggleProject(project))}
        >
          <Icon name={expanded ? 'chevron-down' : 'chevron-right'} size={14} />
          <Icon name="target" size={14} />
          <span class="project-name">{project.name}</span>
          <span
            class="project-count text-muted"
            title={`${stats.found} of ${stats.selected} selected recordings found`}>{stats.found}/{stats.selected}</span
          >
          <button
            type="button"
            class="icon-btn project-close"
            data-testid="flag-project-close-button"
            title={importQueue.importing ? 'Wait for the import to finish' : 'Close this review (nothing is deleted)'}
            aria-label={`Close ${project.name}`}
            disabled={importQueue.importing}
            onclick={(event) => handleClose(event, project)}
          >
            <Icon name="x" size={14} />
          </button>
        </div>
      {/if}

      {#if expanded}
        <div class="project-children" role="group" aria-label={`${project.name} recordings`}>
          {#each projectRecordings(project) as animal (animal.animalId)}
            {@const row = rowsByPath.get(animal.filePath ?? '')}
            {@const openable = canOpen(row)}
            {@const name = row?.filename ?? animal.animalId}
            {@const flagsOpen = !collapsed && animal.flags.length > 0 && flagListOpen(project, animal)}
            <div class="recording">
              <!-- Same idiom as a plain file row: click anywhere, or Enter/Space. -->
              <div
                class="tree-row"
                class:active={animal.filePath === activePath}
                class:clickable={openable}
                data-testid="flag-recording-row"
                title={name}
                role="button"
                tabindex={openable ? 0 : -1}
                aria-disabled={!openable}
                onclick={() => openRecording(project, animal, row)}
                onkeydown={(event) => activateOnKey(event, () => openRecording(project, animal, row))}
              >
                {#if !collapsed}
                  {#if animal.flags.length > 0}
                    <button
                      type="button"
                      class="tree-expand"
                      data-testid="flag-recording-expand"
                      aria-expanded={flagsOpen}
                      aria-label={`${flagsOpen ? 'Hide' : 'Show'} flagged beats of ${name}`}
                      onclick={(event) => toggleFlagList(event, project, animal)}
                    >
                      <Icon name={flagsOpen ? 'chevron-down' : 'chevron-right'} size={12} />
                    </button>
                  {:else}
                    <span class="tree-expand" aria-hidden="true"></span>
                  {/if}
                {/if}
                <SidebarStatusDot status={row?.status ?? 'queued'} />
                {#if !collapsed}
                  <span class="tree-row-body">
                    <span class="tree-row-name">{name}</span>
                    <SidebarRowStatus row={row ?? null} />
                    <span class="flag-summary">
                      {#each kindCounts(animal) as item, index (item.kind)}
                        {#if index > 0}<span class="flag-summary-sep text-muted" aria-hidden="true">·</span>{/if}
                        <span
                          class="flag-summary-item"
                          style:color={`var(${FLAG_KIND_COLOR_VAR[item.kind]})`}
                          title={FLAG_KIND_TITLE[item.kind]}
                          >{item.count} {item.kind === 'flag' ? 'flagged' : FLAG_KIND_LABEL[item.kind]}</span
                        >
                      {/each}
                    </span>
                  </span>
                {/if}
              </div>

              {#if flagsOpen}
                <div class="flag-times" role="group" aria-label={`Flagged beats of ${name}`}>
                  {#each animal.flags as flag (flag.ts)}
                    {@const score = formatPrediction(flag.prediction)}
                    <div
                      class="flag-time"
                      class:active={isActiveFlag(animal, flag)}
                      class:clickable={openable}
                      data-testid="flag-time-row"
                      role="button"
                      tabindex={openable ? 0 : -1}
                      aria-disabled={!openable}
                      onclick={() => openFlag(project, animal, row, flag)}
                      onkeydown={(event) => activateOnKey(event, () => openFlag(project, animal, row, flag))}
                    >
                      <span
                        class="flag-kind"
                        style:color={`var(${FLAG_KIND_COLOR_VAR[flag.kind]})`}
                        style:background={`var(${FLAG_KIND_SOFT_VAR[flag.kind]})`}
                        title={FLAG_KIND_TITLE[flag.kind]}>{FLAG_KIND_LABEL[flag.kind]}</span
                      >
                      <span class="flag-time-value">{formatFlagTime(flag.ts)}</span>
                      {#if score !== null}
                        <span class="flag-prediction text-muted" title="The model's score for this beat">{score}</span>
                      {/if}
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}

          {#if missing > 0 && !collapsed}
            <button
              type="button"
              class="missing-row"
              data-testid="flag-project-missing-row"
              disabled={importQueue.importing}
              title={importQueue.importing ? 'Wait for the import to finish' : undefined}
              onclick={() => handleFind(project)}
            >
              <Icon name="search" size={12} />
              <span>{missing} not found — Find…</span>
            </button>
          {/if}
        </div>
      {/if}
    </div>
  {/each}
</div>

<style>
  .project-tree {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    min-width: 0;
  }

  .project {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .project-root {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    min-width: 0;
    color: var(--color-text);
    cursor: pointer;
    transition: background var(--transition-fast);
  }

  .project-root:hover {
    background: var(--color-surface-hover);
  }

  .project-root :global(svg) {
    flex-shrink: 0;
  }

  .project-name {
    flex: 1;
    min-width: 0;
    font-size: var(--font-size-sm);
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .project-count {
    flex-shrink: 0;
    font-size: 0.7rem;
    font-variant-numeric: tabular-nums;
  }

  /* Collapsed sidebar: the project is just its icon above its recordings' dots. */
  .project-root-icon {
    display: flex;
    justify-content: center;
    padding: var(--space-1) 0;
    color: var(--color-text);
  }

  .project-close {
    flex-shrink: 0;
    width: 22px;
    height: 22px;
    opacity: 0;
    transition: opacity var(--transition-fast), background var(--transition-fast), color var(--transition-fast);
  }

  /* Shown on hover and whenever the row or the X has keyboard focus. */
  .project-root:hover .project-close,
  .project-root:focus-within .project-close {
    opacity: 1;
  }

  /* Closing waits for an import in flight (see handleClose). */
  .project-root .project-close:disabled {
    opacity: 0.4;
    background: transparent;
    color: var(--color-text-muted);
  }

  /* One indent per level, with a faint guide line down the left edge. */
  .project-children {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-left: 14px;
    padding-left: var(--space-1);
    border-left: 1px solid var(--color-border);
    min-width: 0;
  }

  .project-tree.collapsed .project-children {
    margin-left: 0;
    padding-left: 0;
    border-left: none;
  }

  .recording {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .tree-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    border-left: 3px solid transparent;
    min-width: 0;
    transition: background var(--transition-fast);
  }

  .tree-row:hover {
    background: var(--color-surface-hover);
  }

  .tree-row.active {
    background: var(--color-accent-soft);
    border-left-color: var(--color-accent);
  }

  .tree-row.clickable {
    cursor: pointer;
  }

  .project-tree.collapsed .tree-row {
    justify-content: center;
    padding: var(--space-2) 0;
  }

  .tree-expand {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    padding: 0;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text-muted);
  }

  button.tree-expand:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .tree-row-body {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .tree-row-name {
    font-size: var(--font-size-sm);
    font-weight: 500;
    color: var(--color-text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .flag-summary {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    font-size: 0.7rem;
    font-weight: 600;
    white-space: nowrap;
  }

  .flag-times {
    display: flex;
    flex-direction: column;
    gap: 1px;
    margin: 0 0 var(--space-1) 17px;
    padding-left: var(--space-1);
    border-left: 1px solid var(--color-border);
    min-width: 0;
  }

  .flag-time {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 2px var(--space-2);
    border-radius: var(--radius-sm);
    min-width: 0;
    transition: background var(--transition-fast);
  }

  .flag-time:hover {
    background: var(--color-surface-hover);
  }

  .flag-time.active {
    background: var(--color-accent-soft);
  }

  .flag-time.clickable {
    cursor: pointer;
  }

  .flag-kind {
    flex-shrink: 0;
    min-width: 2.4em;
    padding: 0 var(--space-1);
    border-radius: var(--radius-sm);
    font-size: 0.7rem;
    font-weight: 600;
    line-height: 1.5;
    text-align: center;
  }

  .flag-time-value {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    font-variant-numeric: tabular-nums;
    color: var(--color-text);
    white-space: nowrap;
  }

  .flag-prediction {
    margin-left: auto;
    font-size: 0.7rem;
    font-variant-numeric: tabular-nums;
  }

  .missing-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding: var(--space-1) var(--space-2);
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--color-text-muted);
    font-size: 0.75rem;
    text-align: left;
    transition: background var(--transition-fast), color var(--transition-fast);
  }

  .missing-row:hover:not(:disabled) {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .missing-row:disabled {
    opacity: 0.5;
  }
</style>
