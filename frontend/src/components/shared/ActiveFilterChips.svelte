<script lang="ts">
  import {
    FACET_TITLES,
    REVIEW_GROUP_LABELS,
    SORT_LABELS,
    clearFacets,
    folderName,
    type FileFilter,
    type SortChoice,
  } from '../../lib/fileFacts'
  import Icon from './Icon.svelte'

  // What a list is sorted and filtered by, as small chips under its search
  // field; each one's x takes it off. Shown only when something differs from
  // the list's defaults.
  let {
    filter,
    defaultSort,
    statusLabels = {},
    testid = 'active-filters',
    compact = false,
  }: {
    filter: FileFilter
    defaultSort: SortChoice
    statusLabels?: Record<string, string>
    testid?: string
    // The sidebar: smaller chips, no facet names.
    compact?: boolean
  } = $props()

  interface Chip {
    key: string
    label: string
    title: string
    remove: () => void
  }

  let chips: Chip[] = $derived.by(() => {
    const out: Chip[] = []
    const named = (facet: keyof typeof FACET_TITLES, value: string) =>
      compact ? value : `${FACET_TITLES[facet]}: ${value}`
    if (filter.sort.key !== defaultSort.key || filter.sort.dir !== defaultSort.dir) {
      const arrow = filter.sort.dir === 'asc' ? '↑' : '↓'
      out.push({
        key: 'sort',
        label: `${compact ? '' : 'Sort: '}${SORT_LABELS[filter.sort.key]} ${arrow}`,
        title: `Sorted by ${SORT_LABELS[filter.sort.key].toLowerCase()}; remove to go back to the usual order`,
        remove: () => (filter.sort = { ...defaultSort }),
      })
    }
    for (const group of filter.review) {
      out.push({
        key: `review:${group}`,
        label: named('review', REVIEW_GROUP_LABELS[group]),
        title: 'Remove this filter',
        remove: () => (filter.review = filter.review.filter((v) => v !== group)),
      })
    }
    if (filter.uncertainOnly) {
      out.push({
        key: 'uncertain',
        label: 'Uncertain beats',
        title: 'Remove this filter',
        remove: () => (filter.uncertainOnly = false),
      })
    }
    for (const value of filter.status) {
      out.push({
        key: `status:${value}`,
        label: named('status', statusLabels[value] ?? value),
        title: 'Remove this filter',
        remove: () => (filter.status = filter.status.filter((v) => v !== value)),
      })
    }
    for (const value of filter.channels) {
      out.push({
        key: `channels:${value}`,
        label: compact ? value : `Channel: ${value}`,
        title: 'Remove this filter',
        remove: () => (filter.channels = filter.channels.filter((v) => v !== value)),
      })
    }
    for (const value of filter.types) {
      out.push({
        key: `types:${value}`,
        label: named('types', value),
        title: 'Remove this filter',
        remove: () => (filter.types = filter.types.filter((v) => v !== value)),
      })
    }
    for (const value of filter.folders) {
      out.push({
        key: `folders:${value}`,
        label: named('folders', folderName(value) || value),
        title: value,
        remove: () => (filter.folders = filter.folders.filter((v) => v !== value)),
      })
    }
    return out
  })

  function clearAll() {
    clearFacets(filter)
    filter.sort = { ...defaultSort }
  }
</script>

{#if chips.length > 0}
  <div class="afc" class:afc-compact={compact} data-testid={testid}>
    {#each chips as chip (chip.key)}
      <span class="afc-chip" title={chip.title}>
        <span class="afc-label">{chip.label}</span>
        <button
          type="button"
          class="afc-remove"
          data-testid={`${testid}-remove`}
          aria-label={`Remove ${chip.label}`}
          onclick={chip.remove}
        >
          <Icon name="x" size={10} />
        </button>
      </span>
    {/each}
    {#if chips.length > 1}
      <button type="button" class="afc-clear" data-testid={`${testid}-clear`} onclick={clearAll}>Clear all</button>
    {/if}
  </div>
{/if}

<style>
  .afc {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1);
  }

  .afc-chip {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    max-width: 100%;
    padding: 1px 2px 1px var(--space-2);
    border: 1px solid var(--color-accent);
    border-radius: 999px;
    background: var(--color-accent-soft);
    color: var(--color-text);
    font-size: 0.75rem;
  }

  .afc-compact .afc-chip {
    font-size: 0.7rem;
  }

  .afc-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .afc-remove {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    flex-shrink: 0;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: var(--color-text-muted);
  }

  .afc-remove:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }

  .afc-clear {
    padding: 0 var(--space-1);
    border: none;
    background: none;
    color: var(--color-accent);
    font-size: 0.75rem;
  }

  .afc-clear:hover {
    text-decoration: underline;
  }
</style>
