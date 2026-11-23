<script lang="ts">
  import { onMount, tick } from 'svelte'
  import {
    DEFAULT_SORT_DIR,
    FACET_TITLES,
    SORT_LABELS,
    activeFacetCount,
    clearFacets,
    facetNarrows,
    facetOptions,
    sortDirLabel,
    type FacetId,
    type FileFacts,
    type FileFilter,
    type ReviewGroup,
    type SortChoice,
    type SortKey,
  } from '../../lib/fileFacts'
  import Icon from './Icon.svelte'

  // The "Sort & filter" button and its panel, shared by the sidebar and Home:
  // a sort (field and direction) and the facets that narrow the list, each a
  // row of toggle chips with how many files it would leave. Works directly on
  // the list's `filter` (module state owned by that list). Positioned against
  // the window, like MenuButton, so the sidebar's clipping can't cut it off.
  let {
    filter,
    facts,
    sortKeys,
    defaultSort,
    facets,
    statusLabels = {},
    shown,
    total,
    loading = false,
    variant = 'icon',
    testid = 'sort-filter',
    onOpenChange,
  }: {
    filter: FileFilter
    // The list after its search words: what the counts are taken over.
    facts: FileFacts[]
    sortKeys: SortKey[]
    defaultSort: SortChoice
    facets: FacetId[]
    // Labels of the list's own status groups, in order.
    statusLabels?: Record<string, string>
    // How many files the list shows, and has, for the footer.
    shown: number
    total: number
    // Saved-review counts are on their way.
    loading?: boolean
    // 'icon': a small square button (the sidebar); 'button': labelled (Home).
    variant?: 'icon' | 'button'
    testid?: string
    onOpenChange?: (open: boolean) => void
  } = $props()

  const uid = $props.id()

  let open = $state(false)
  let triggerEl: HTMLButtonElement | undefined = $state()
  let panelEl: HTMLDivElement | undefined = $state()
  let position = $state({ top: 0, left: 0, maxHeight: 480 })

  let active = $derived(activeFacetCount(filter))
  let sortChanged = $derived(filter.sort.key !== defaultSort.key || filter.sort.dir !== defaultSort.dir)
  let canReset = $derived(active > 0 || sortChanged)

  interface ShownFacet {
    id: FacetId
    options: ReturnType<typeof facetOptions>
  }

  function selectedOf(id: FacetId): string[] {
    return id === 'review' ? filter.review : filter[id]
  }

  // Recordings whose channels aren't known (see FileFacts.channels).
  let unknownChannels = $derived(facts.filter((item) => item.channels === null).length)

  // Review is always offered (its counts may still be loading); the others
  // only when they can narrow this list, or something in them is picked.
  let shownFacets: ShownFacet[] = $derived(
    open
      ? facets
          .filter(
            (id) =>
              id === 'review' ||
              selectedOf(id).length > 0 ||
              facetNarrows(facts, id) ||
              (id === 'channels' && unknownChannels > 0),
          )
          .map((id) => ({ id, options: facetOptions(facts, filter, id, statusLabels) }))
      : [],
  )

  function toggleValue(id: FacetId, value: string) {
    if (id === 'review') {
      const group = value as ReviewGroup
      filter.review = filter.review.includes(group) ? filter.review.filter((v) => v !== group) : [...filter.review, group]
      return
    }
    const current = filter[id]
    filter[id] = current.includes(value) ? current.filter((v) => v !== value) : [...current, value]
  }

  function clearFacet(id: FacetId) {
    if (id === 'review') {
      filter.review = []
      filter.uncertainOnly = false
    } else {
      filter[id] = []
    }
  }

  function setSortKey(key: SortKey) {
    // A new field starts in its natural direction (names A to Z, counts most first).
    filter.sort = { key, dir: DEFAULT_SORT_DIR[key] }
  }

  function flipDir() {
    filter.sort = { key: filter.sort.key, dir: filter.sort.dir === 'asc' ? 'desc' : 'asc' }
  }

  function resetAll() {
    clearFacets(filter)
    filter.sort = { ...defaultSort }
  }

  async function openPanel() {
    if (!triggerEl) return
    open = true
    onOpenChange?.(true)
    place()
    await tick()
    place()
    panelEl?.querySelector<HTMLElement>('select, button:not(:disabled), input')?.focus()
  }

  // Below the button, kept on screen, as tall as the window allows.
  function place() {
    if (!triggerEl) return
    const rect = triggerEl.getBoundingClientRect()
    const width = panelEl?.getBoundingClientRect().width ?? 320
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - 8 - width))
    const top = rect.bottom + 4
    position = { top, left, maxHeight: Math.max(240, window.innerHeight - top - 12) }
  }

  function close(refocus: boolean) {
    if (!open) return
    open = false
    onOpenChange?.(false)
    if (refocus) triggerEl?.focus()
  }

  function toggle() {
    if (open) close(true)
    else void openPanel()
  }

  function onPanelKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault()
      close(true)
    }
    // Keys typed here are the panel's: the review screen's shortcuts (C, R,
    // N, arrows...) must not act on them.
    event.stopPropagation()
  }

  // Tabbing out of the panel closes it, like leaving a menu.
  function onPanelFocusOut(event: FocusEvent) {
    const next = event.relatedTarget as Node | null
    if (next && (panelEl?.contains(next) || triggerEl?.contains(next))) return
    if (next) close(false)
  }

  onMount(() => {
    function onPointerDown(event: PointerEvent) {
      if (!open) return
      const target = event.target as Node | null
      if (target && (panelEl?.contains(target) || triggerEl?.contains(target))) return
      close(false)
    }
    function onResize() {
      if (open) place()
    }
    window.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('resize', onResize)
      if (open) onOpenChange?.(false)
    }
  })

  let triggerLabel = $derived(
    active > 0 ? `Sort & filter (${active} filter${active === 1 ? '' : 's'} on)` : 'Sort & filter',
  )
</script>

<button
  type="button"
  class={variant === 'icon' ? 'icon-btn sfp-trigger sfp-trigger-icon' : 'btn btn-sm sfp-trigger'}
  class:sfp-trigger-on={active > 0}
  data-testid={testid}
  aria-haspopup="dialog"
  aria-expanded={open}
  aria-controls={open ? `${uid}-panel` : undefined}
  aria-label={triggerLabel}
  title={triggerLabel}
  bind:this={triggerEl}
  onclick={toggle}
>
  <Icon name="sliders" size={14} />
  {#if variant === 'button'}<span>Sort &amp; filter</span>{/if}
  {#if active > 0}<span class="sfp-badge" data-testid={`${testid}-count`}>{active}</span>{/if}
</button>

{#if open}
  <div
    class="sfp-panel"
    id={`${uid}-panel`}
    role="dialog"
    aria-label="Sort and filter"
    tabindex="-1"
    data-testid={`${testid}-panel`}
    style:top={`${position.top}px`}
    style:left={`${position.left}px`}
    style:max-height={`${position.maxHeight}px`}
    bind:this={panelEl}
    onkeydown={onPanelKeydown}
    onfocusout={onPanelFocusOut}
  >
    <div class="sfp-head">
      <span class="sfp-title">Sort &amp; filter</span>
      <button
        type="button"
        class="btn btn-sm btn-ghost"
        data-testid={`${testid}-reset`}
        disabled={!canReset}
        title="Back to the default order, with no filters"
        onclick={resetAll}
      >
        Reset
      </button>
    </div>

    <div class="sfp-body">
      <section class="sfp-section">
        <h4 class="sfp-heading" id={`${uid}-sort`}>Sort by</h4>
        <div class="sfp-sort">
          <select
            aria-labelledby={`${uid}-sort`}
            data-testid={`${testid}-sort-key`}
            value={filter.sort.key}
            onchange={(event) => setSortKey((event.currentTarget as HTMLSelectElement).value as SortKey)}
          >
            {#each sortKeys as key (key)}
              <option value={key}>{SORT_LABELS[key]}</option>
            {/each}
          </select>
          <button
            type="button"
            class="btn btn-sm sfp-dir"
            data-testid={`${testid}-sort-dir`}
            title="Reverse the order"
            aria-label={`${sortDirLabel(filter.sort.key, filter.sort.dir)}; reverse the order`}
            onclick={flipDir}
          >
            <Icon name={filter.sort.dir === 'asc' ? 'arrow-up' : 'arrow-down'} size={14} />
            {sortDirLabel(filter.sort.key, filter.sort.dir)}
          </button>
        </div>
      </section>

      {#each shownFacets as facet (facet.id)}
        {@const picked = selectedOf(facet.id)}
        <section class="sfp-section" role="group" aria-labelledby={`${uid}-${facet.id}`}>
          <div class="sfp-heading-row">
            <h4 class="sfp-heading" id={`${uid}-${facet.id}`}>{FACET_TITLES[facet.id]}</h4>
            {#if picked.length > 0 || (facet.id === 'review' && filter.uncertainOnly)}
              <button type="button" class="sfp-link" onclick={() => clearFacet(facet.id)}>Clear</button>
            {/if}
          </div>
          {#if facet.options.length > 0}
            <div class="sfp-chips" class:sfp-chips-scroll={facet.options.length > 10}>
              {#each facet.options as option (option.value)}
                {@const on = picked.includes(option.value)}
                <button
                  type="button"
                  class="sfp-chip"
                  class:on
                  aria-pressed={on}
                  disabled={!on && option.count === 0}
                  title={option.title ?? option.label}
                  data-testid={`${testid}-${facet.id}-option`}
                  data-value={option.value}
                  onclick={() => toggleValue(facet.id, option.value)}
                >
                  <span class="sfp-chip-label">{option.label}</span>
                  <span class="sfp-chip-count">{option.count}</span>
                </button>
              {/each}
            </div>
          {/if}
          {#if facet.id === 'review'}
            <label class="sfp-check">
              <input
                type="checkbox"
                data-testid={`${testid}-uncertain`}
                checked={filter.uncertainOnly}
                onchange={(event) => (filter.uncertainOnly = (event.currentTarget as HTMLInputElement).checked)}
              />
              Only files with uncertain beats
            </label>
            {#if loading}<p class="sfp-note">Counting saved reviews…</p>{/if}
          {/if}
          {#if facet.id === 'channels' && unknownChannels > 0}
            <p class="sfp-note">
              {unknownChannels === 1 ? '1 recording has' : `${unknownChannels} recordings have`} no known channels yet; they
              show once the file is opened again.
            </p>
          {/if}
        </section>
      {/each}
    </div>

    <div class="sfp-foot">
      <span class="text-muted" data-testid={`${testid}-shown`}>
        {shown === total ? `${total} ${total === 1 ? 'file' : 'files'}` : `${shown} of ${total} files shown`}
      </span>
      <button type="button" class="btn btn-sm btn-primary" data-testid={`${testid}-done`} onclick={() => close(true)}>
        Done
      </button>
    </div>
  </div>
{/if}

<style>
  .sfp-trigger {
    position: relative;
    gap: var(--space-1);
  }

  .sfp-trigger-icon {
    width: 30px;
    height: 30px;
  }

  /* A filter is on: mark the button so a short list isn't a mystery. */
  .sfp-trigger-on {
    border-color: var(--color-accent);
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }

  .sfp-badge {
    min-width: 16px;
    padding: 0 4px;
    border-radius: 999px;
    background: var(--color-accent);
    color: var(--color-on-accent, #fff);
    font-size: 0.65rem;
    font-weight: 700;
    line-height: 16px;
    text-align: center;
  }

  .sfp-trigger-icon .sfp-badge {
    position: absolute;
    top: -5px;
    right: -5px;
  }

  .sfp-panel {
    position: fixed;
    z-index: 60;
    display: flex;
    flex-direction: column;
    width: min(340px, calc(100vw - 16px));
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-raised);
    box-shadow: var(--shadow-md);
    color: var(--color-text);
    outline: none;
  }

  .sfp-head,
  .sfp-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
  }

  .sfp-head {
    border-bottom: 1px solid var(--color-border);
  }

  .sfp-foot {
    border-top: 1px solid var(--color-border);
    font-size: var(--font-size-sm);
  }

  .sfp-title {
    font-weight: 600;
    font-size: var(--font-size-sm);
  }

  .sfp-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: var(--space-1) var(--space-3) var(--space-3);
  }

  .sfp-section {
    padding-top: var(--space-3);
  }

  .sfp-heading-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .sfp-heading {
    margin: 0 0 var(--space-2);
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-muted);
  }

  .sfp-heading-row .sfp-heading {
    margin-bottom: var(--space-2);
  }

  .sfp-link {
    margin-bottom: var(--space-2);
    padding: 0;
    border: none;
    background: none;
    color: var(--color-accent);
    font-size: 0.75rem;
  }

  .sfp-link:hover {
    text-decoration: underline;
  }

  .sfp-sort {
    display: flex;
    gap: var(--space-2);
  }

  .sfp-sort select {
    flex: 1;
    min-width: 0;
    padding: 3px var(--space-2);
    font-size: var(--font-size-sm);
  }

  .sfp-dir {
    flex-shrink: 0;
    gap: var(--space-1);
    white-space: nowrap;
  }

  .sfp-chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
  }

  /* A long list (many channels or folders) scrolls instead of filling the panel. */
  .sfp-chips-scroll {
    max-height: 132px;
    overflow-y: auto;
    padding-right: var(--space-1);
  }

  .sfp-chip {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    max-width: 100%;
    padding: 2px var(--space-2);
    border: 1px solid var(--color-border);
    border-radius: 999px;
    background: var(--color-surface);
    color: var(--color-text-muted);
    font-size: 0.75rem;
    font-weight: 500;
    transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
  }

  .sfp-chip:hover:not(:disabled) {
    color: var(--color-text);
    background: var(--color-surface-hover);
  }

  .sfp-chip.on,
  .sfp-chip.on:hover {
    border-color: var(--color-accent);
    background: var(--color-accent-soft);
    color: var(--color-text);
  }

  .sfp-chip:disabled {
    opacity: 0.45;
  }

  .sfp-chip-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sfp-chip-count {
    font-variant-numeric: tabular-nums;
    color: var(--color-text-muted);
  }

  .sfp-check {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-top: var(--space-2);
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
  }

  .sfp-note {
    margin: var(--space-2) 0 0;
    font-size: 0.75rem;
    color: var(--color-text-muted);
  }
</style>
