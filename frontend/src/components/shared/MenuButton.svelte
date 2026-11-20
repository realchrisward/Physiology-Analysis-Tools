<script module lang="ts">
  export interface MenuItem {
    id: string
    label: string
    icon?: string
    hint?: string
    disabled?: boolean
    // Set (true/false) for a choice among options: shown with a mark, and
    // announced as a radio item.
    checked?: boolean
    testid?: string
    onSelect: () => void
  }
</script>

<script lang="ts">
  import { onMount, tick, type Snippet } from 'svelte'
  import Icon from './Icon.svelte'

  // A button that opens a small menu. The menu is positioned against the
  // window (position: fixed), so a sidebar that clips its overflow, collapsed
  // to a rail of icons, can still show it beside the button.
  let {
    items,
    trigger,
    label,
    testid,
    triggerClass = 'btn btn-sm',
    disabled = false,
    placement = 'below',
  }: {
    items: MenuItem[]
    trigger: Snippet
    // The button's accessible name and tooltip.
    label: string
    testid?: string
    triggerClass?: string
    disabled?: boolean
    // 'right' opens beside the button (a collapsed sidebar).
    placement?: 'below' | 'right'
  } = $props()

  const uid = $props.id()

  let open = $state(false)
  let triggerEl: HTMLButtonElement | undefined = $state()
  let menuEl: HTMLDivElement | undefined = $state()
  let position = $state({ top: 0, left: 0 })

  function enabledItems(): HTMLButtonElement[] {
    return Array.from(menuEl?.querySelectorAll<HTMLButtonElement>('.menu-item:not(:disabled)') ?? [])
  }

  function focusItem(index: number) {
    const elements = enabledItems()
    if (elements.length === 0) return
    elements[((index % elements.length) + elements.length) % elements.length].focus()
  }

  async function openMenu(focusIndex: number) {
    if (disabled || !triggerEl) return
    const rect = triggerEl.getBoundingClientRect()
    position =
      placement === 'right' ? { top: rect.top, left: rect.right + 4 } : { top: rect.bottom + 4, left: rect.left }
    open = true
    await tick()
    if (!menuEl) return
    // Keep it on screen.
    const box = menuEl.getBoundingClientRect()
    if (box.right > window.innerWidth - 8) position.left = Math.max(8, window.innerWidth - 8 - box.width)
    if (box.bottom > window.innerHeight - 8) position.top = Math.max(8, window.innerHeight - 8 - box.height)
    focusItem(focusIndex)
  }

  function close(refocus: boolean) {
    if (!open) return
    open = false
    if (refocus) triggerEl?.focus()
  }

  function toggle() {
    if (open) close(true)
    else void openMenu(0)
  }

  function onTriggerKeydown(event: KeyboardEvent) {
    // Focus stays on the button when every item is disabled.
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      close(true)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      void openMenu(0)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      void openMenu(-1)
    }
  }

  function onMenuKeydown(event: KeyboardEvent) {
    const elements = enabledItems()
    const current = elements.indexOf(document.activeElement as HTMLButtonElement)
    let handled = true
    if (event.key === 'ArrowDown') focusItem(current + 1)
    else if (event.key === 'ArrowUp') focusItem(current - 1)
    else if (event.key === 'Home') focusItem(0)
    else if (event.key === 'End') focusItem(-1)
    else if (event.key === 'Escape') close(true)
    else if (event.key === 'Tab') {
      close(false)
      handled = false
    } else handled = false
    if (handled) {
      // The keys are the menu's: the graph's own shortcuts must not see them.
      event.preventDefault()
      event.stopPropagation()
    }
  }

  function select(item: MenuItem) {
    if (item.disabled) return
    close(true)
    item.onSelect()
  }

  onMount(() => {
    function onPointerDown(event: PointerEvent) {
      if (!open) return
      const target = event.target as Node | null
      if (target && (menuEl?.contains(target) || triggerEl?.contains(target))) return
      close(false)
    }
    function onDismiss() {
      close(false)
    }
    window.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('resize', onDismiss)
    window.addEventListener('blur', onDismiss)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('resize', onDismiss)
      window.removeEventListener('blur', onDismiss)
    }
  })
</script>

<button
  type="button"
  class={triggerClass}
  data-testid={testid}
  aria-haspopup="menu"
  aria-expanded={open}
  aria-controls={open ? `${uid}-menu` : undefined}
  aria-label={label}
  title={label}
  {disabled}
  bind:this={triggerEl}
  onclick={toggle}
  onkeydown={onTriggerKeydown}
>
  {@render trigger()}
</button>

{#if open}
  <div
    class="menu"
    id={`${uid}-menu`}
    role="menu"
    tabindex="-1"
    aria-label={label}
    style:top={`${position.top}px`}
    style:left={`${position.left}px`}
    bind:this={menuEl}
    onkeydown={onMenuKeydown}
  >
    {#each items as item (item.id)}
      <button
        type="button"
        class="menu-item"
        class:checked={item.checked}
        role={item.checked === undefined ? 'menuitem' : 'menuitemradio'}
        aria-checked={item.checked}
        data-testid={item.testid}
        title={item.hint}
        disabled={item.disabled}
        tabindex="-1"
        onclick={() => select(item)}
      >
        {#if item.checked !== undefined}
          <span class="mark" aria-hidden="true">{#if item.checked}<span class="dot"></span>{/if}</span>
        {/if}
        {#if item.icon}<Icon name={item.icon} size={14} />{/if}
        <span class="menu-label">{item.label}</span>
      </button>
    {/each}
  </div>
{/if}

<style>
  .menu {
    position: fixed;
    z-index: 60;
    display: flex;
    flex-direction: column;
    min-width: 180px;
    padding: var(--space-1);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-raised);
    box-shadow: var(--shadow-md);
    outline: none;
  }

  .menu-item {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding: var(--space-2) var(--space-3);
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--color-text);
    font-size: var(--font-size-sm);
    text-align: left;
    white-space: nowrap;
  }

  .menu-item:hover:not(:disabled),
  .menu-item:focus-visible {
    background: var(--color-surface-hover);
    outline: none;
  }

  .menu-item:disabled {
    opacity: 0.5;
  }

  .menu-item :global(svg) {
    flex-shrink: 0;
    color: var(--color-text-muted);
  }

  .mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    flex-shrink: 0;
  }

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--color-accent);
  }

  .menu-item.checked {
    font-weight: 600;
  }
</style>
