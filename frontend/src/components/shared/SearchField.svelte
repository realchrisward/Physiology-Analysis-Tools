<script lang="ts">
  import Icon from './Icon.svelte'

  // A search box: magnifier, the input, and a clear button once there is text.
  // Escape clears it (a second Escape on an empty box is left to the page).
  let {
    value = $bindable(''),
    placeholder = 'Search…',
    label = 'Search',
    testid = 'search-field',
    size = 'md',
    input = $bindable(),
  }: {
    value?: string
    placeholder?: string
    // The accessible name; the placeholder is only a hint.
    label?: string
    testid?: string
    size?: 'sm' | 'md'
    input?: HTMLInputElement
  } = $props()

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape' && value) {
      event.preventDefault()
      event.stopPropagation()
      value = ''
    }
  }

  function clear() {
    value = ''
    input?.focus()
  }
</script>

<div class="search-field" class:sm={size === 'sm'}>
  <span class="search-icon" aria-hidden="true"><Icon name="search" size={size === 'sm' ? 14 : 16} /></span>
  <input
    type="search"
    class="search-input"
    data-testid={testid}
    aria-label={label}
    {placeholder}
    autocomplete="off"
    spellcheck="false"
    bind:value
    bind:this={input}
    onkeydown={handleKeydown}
  />
  {#if value}
    <button
      type="button"
      class="search-clear"
      data-testid={`${testid}-clear`}
      aria-label="Clear search"
      title="Clear search"
      onclick={clear}
    >
      <Icon name="x" size={size === 'sm' ? 12 : 14} />
    </button>
  {/if}
</div>

<style>
  .search-field {
    position: relative;
    display: flex;
    align-items: center;
    min-width: 0;
    flex: 1;
  }

  .search-icon {
    position: absolute;
    left: var(--space-3);
    display: flex;
    color: var(--color-text-muted);
    pointer-events: none;
  }

  .search-input {
    width: 100%;
    min-width: 0;
    padding: var(--space-2) calc(var(--space-3) + 20px) var(--space-2) calc(var(--space-3) + 24px);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-text);
    font: inherit;
    font-size: var(--font-size-sm);
    transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
  }

  .search-input::placeholder {
    color: var(--color-text-muted);
  }

  .search-input:focus {
    outline: none;
    border-color: var(--color-accent);
    box-shadow: 0 0 0 3px var(--color-accent-soft);
  }

  /* The native clear button: ours is consistent across platforms. */
  .search-input::-webkit-search-cancel-button {
    display: none;
  }

  .sm .search-icon {
    left: var(--space-2);
  }

  .sm .search-input {
    padding: 6px calc(var(--space-2) + 18px) 6px calc(var(--space-2) + 20px);
    font-size: 0.8125rem;
  }

  .search-clear {
    position: absolute;
    right: var(--space-1);
    display: flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    padding: 0;
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--color-text-muted);
  }

  .search-clear:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
  }
</style>
