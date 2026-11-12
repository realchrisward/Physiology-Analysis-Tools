<script lang="ts">
  import { onMount } from 'svelte'
  import { putChannelLabel } from '../../lib/api/persistence'
  import { applyChannelLabels, channelLabel, fileRegistry } from '../../lib/stores/fileRegistry.svelte'

  // Renames a channel for display: in the open file only, or in any of the
  // other files in this workspace that have the same channel. The channel's
  // own name stays its key for detection and saved review data, so renaming
  // never touches either. Names are saved in the backend.
  //   path    - the open file.
  //   channel - the channel's own name (not its display name).
  //   onClose - after a save, a reset, a cancel, Escape or a backdrop click.
  let { path, channel, onClose }: { path: string; channel: string; onClose: () => void } = $props()

  const MAX_LENGTH = 40

  let currentRow = $derived(fileRegistry.find((row) => row.path === path))
  // Other files in the workspace that have a channel of this name.
  let otherRows = $derived(
    fileRegistry.filter((row) => row.path !== path && row.status !== 'error' && row.channels.includes(channel)),
  )

  // Seeded once from the open file; the dialog is remounted for each rename.
  // svelte-ignore state_referenced_locally
  let name = $state(channelLabel(currentRow, channel))
  let scope: 'this' | 'choose' = $state('this')
  // svelte-ignore state_referenced_locally
  let chosen: Set<string> = $state(new Set(otherRows.map((row) => row.path)))
  let busy = $state(false)
  let error: string | null = $state(null)
  let input: HTMLInputElement | undefined = $state()

  let targetPaths = $derived(scope === 'this' ? [path] : [path, ...otherRows.filter((r) => chosen.has(r.path)).map((r) => r.path)])
  let anyRenamed = $derived(
    targetPaths.some((p) => fileRegistry.find((row) => row.path === p)?.channelLabels?.[channel] !== undefined),
  )
  let allChosen = $derived(otherRows.every((row) => chosen.has(row.path)))

  onMount(() => {
    input?.focus()
    input?.select()
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) onClose()
    }
    document.addEventListener('keydown', handleKeydown)
    return () => document.removeEventListener('keydown', handleKeydown)
  })

  function toggle(rowPath: string) {
    const next = new Set(chosen)
    if (next.has(rowPath)) next.delete(rowPath)
    else next.add(rowPath)
    chosen = next
  }

  function toggleAll() {
    chosen = allChosen ? new Set() : new Set(otherRows.map((row) => row.path))
  }

  async function save(label: string | null) {
    if (busy) return
    busy = true
    error = null
    const result = await putChannelLabel(targetPaths, channel, label)
    busy = false
    if (result.status === 'ok' && !result.error) {
      applyChannelLabels(result.labels)
      onClose()
    } else {
      error = result.error ?? 'Could not save the channel name'
    }
  }

  function handleSubmit(event: SubmitEvent) {
    event.preventDefault()
    void save(name.trim() || null)
  }

  function handleBackdropClick(event: MouseEvent) {
    if (event.target === event.currentTarget && !busy) onClose()
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- Escape is handled by the document listener above; the backdrop only
     needs to catch a direct click on itself. -->
<div class="rename-backdrop" data-testid="channel-rename-backdrop" onclick={handleBackdropClick}>
  <form
    class="rename-dialog"
    data-testid="channel-rename-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="channel-rename-title"
    onsubmit={handleSubmit}
  >
    <h2 id="channel-rename-title">Rename channel</h2>

    <label class="field">
      <span class="field-label">Name</span>
      <input
        type="text"
        data-testid="channel-rename-input"
        bind:this={input}
        bind:value={name}
        maxlength={MAX_LENGTH}
        placeholder={channel}
        autocomplete="off"
        spellcheck="false"
      />
      <span class="hint">
        Original name: <strong>{channel}</strong>. Only the name shown changes; detection and saved review work are not
        affected.
      </span>
    </label>

    {#if otherRows.length > 0}
      <fieldset class="scope" data-testid="channel-rename-scope">
        <legend class="field-label">Apply to</legend>
        <label class="radio">
          <input type="radio" name="scope" value="this" bind:group={scope} data-testid="channel-rename-scope-this" />
          This file only <span class="text-muted">({currentRow?.filename ?? path})</span>
        </label>
        <label class="radio">
          <input type="radio" name="scope" value="choose" bind:group={scope} data-testid="channel-rename-scope-choose" />
          This file and other files with “{channel}”
          <span class="text-muted">({otherRows.length} more in this workspace)</span>
        </label>

        {#if scope === 'choose'}
          <div class="file-list" data-testid="channel-rename-file-list">
            <label class="file-row select-all">
              <input type="checkbox" checked={allChosen} onchange={toggleAll} data-testid="channel-rename-select-all" />
              Select all
            </label>
            {#each otherRows as row (row.path)}
              <label class="file-row" title={row.path}>
                <input type="checkbox" checked={chosen.has(row.path)} onchange={() => toggle(row.path)} />
                <span class="file-name">{row.filename}</span>
                {#if row.channelLabels?.[channel]}
                  <span class="text-muted current-name">now “{row.channelLabels[channel]}”</span>
                {/if}
              </label>
            {/each}
          </div>
        {/if}
      </fieldset>
    {/if}

    {#if error}
      <p class="text-danger error" data-testid="channel-rename-error">{error}</p>
    {/if}

    <div class="rename-footer">
      {#if anyRenamed}
        <button
          type="button"
          class="btn btn-ghost reset"
          data-testid="channel-rename-reset"
          disabled={busy}
          onclick={() => save(null)}
        >
          Reset to “{channel}”
        </button>
      {/if}
      <button type="button" class="btn" data-testid="channel-rename-cancel" disabled={busy} onclick={onClose}>
        Cancel
      </button>
      <button type="submit" class="btn btn-primary" data-testid="channel-rename-save" disabled={busy}>
        {targetPaths.length > 1 ? `Save for ${targetPaths.length} files` : 'Save'}
      </button>
    </div>
  </form>
</div>

<style>
  .rename-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-4);
    z-index: 100;
  }

  .rename-dialog {
    width: min(480px, 100%);
    max-height: calc(100vh - 2 * var(--space-4));
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--color-surface-raised);
    color: var(--color-text);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
  }

  .rename-dialog h2 {
    margin: 0;
    font-size: var(--font-size-lg);
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .field-label {
    font-size: var(--font-size-sm);
    font-weight: 600;
  }

  .hint {
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
    line-height: 1.4;
  }

  .scope {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin: 0;
    padding: 0;
    border: none;
  }

  .scope legend {
    padding: 0;
    margin-bottom: var(--space-2);
  }

  .radio,
  .file-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--font-size-sm);
  }

  .file-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    max-height: 220px;
    overflow-y: auto;
    margin-left: var(--space-5, 24px);
    padding: var(--space-2);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
  }

  .select-all {
    font-weight: 600;
    padding-bottom: var(--space-1);
    border-bottom: 1px solid var(--color-border);
  }

  .file-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .current-name {
    margin-left: auto;
    white-space: nowrap;
  }

  .error {
    margin: 0;
    font-size: var(--font-size-sm);
  }

  .rename-footer {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-2);
  }

  .rename-footer .reset {
    margin-right: auto;
  }
</style>
