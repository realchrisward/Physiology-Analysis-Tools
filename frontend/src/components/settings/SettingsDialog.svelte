<script lang="ts">
  import { onMount } from 'svelte'
  import { getSettings, putSettings } from '../../lib/api/settings'
  import type { ArrhythmiaSettingsModel, BeatSettingsModel } from '../../lib/api/types'
  import Icon from '../shared/Icon.svelte'
  import { setTheme, themeState, type ThemeMode } from '../../lib/stores/theme.svelte'

  let { onClose }: { onClose: () => void } = $props()

  const THEME_OPTIONS: { mode: ThemeMode; label: string; icon: string }[] = [
    { mode: 'system', label: 'System', icon: 'monitor' },
    { mode: 'light', label: 'Light', icon: 'sun' },
    { mode: 'dark', label: 'Dark', icon: 'moon' },
    { mode: 'bw', label: 'Black & White', icon: 'contrast' },
  ]

  // Single state value for the mount-time load, same "one clear state value"
  // discipline used elsewhere (ImportScreen's `queueState`,
  // ArrhythmiaControls' `runState`) rather than separate booleans.
  //   'loading' - GET /settings in flight; form not yet populated.
  //   'loaded'  - form populated from the backend; editable.
  //   'error'   - GET /settings failed; nothing to edit.
  type LoadState = 'loading' | 'loaded' | 'error'
  let loadState: LoadState = $state('loading')
  let loadError: string | null = $state(null)

  // Same discipline for the save lifecycle.
  type SaveState = 'idle' | 'saving'
  let saveState: SaveState = $state('idle')
  let saveError: string | null = $state(null)

  // Local form state, populated from GET /settings on mount. Field names
  // match BeatSettingsModel/ArrhythmiaSettingsModel exactly (see
  // lib/api/types.ts) since the Save button round-trips this object
  // straight back as the PUT body.
  let beat: BeatSettingsModel = $state({
    min_RR: 0,
    ecg_invert: false,
    auto_detect_invert: false,
    ecg_filter: false,
    ecg_filt_order: 0,
    ecg_filt_cutoff: 0,
    abs_thresh: null,
    perc_thresh: null,
  })

  let arrhythmia: ArrhythmiaSettingsModel = $state({
    bradycardia_absolute_hr: 0,
    tachycardia_absolute_hr: 0,
    skipped_beat_multiple_rr: 0,
    premature_beat_multiple_rr: 0,
    window_size: 0,
    eps: 0,
    min_samples: 0,
  })

  onMount(async () => {
    const result = await getSettings()
    if ('error' in result) {
      loadState = 'error'
      loadError = result.error
      return
    }
    beat = { ...result.beat }
    arrhythmia = { ...result.arrhythmia }
    loadState = 'loaded'
  })

  // Dismiss via Escape, in addition to the backdrop click and `×` button
  // below — all three call the same `onClose` prop. A separate, synchronous
  // `onMount` (not the async load one above) so its returned function is
  // actually treated as unmount cleanup by Svelte.
  onMount(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeydown)
    return () => document.removeEventListener('keydown', handleKeydown)
  })

  // Only closes when the backdrop itself (not a click bubbling up from the
  // card) was the actual click target.
  function handleBackdropClick(event: MouseEvent) {
    if (event.target === event.currentTarget) onClose()
  }

  // On a successful save the dialog closes (rather than staying open with a
  // success message) — the technician opened it to make a change and Save
  // confirms that change went through; there's nothing further to review in
  // the dialog itself. A failed save (validation error from the backend)
  // stays open, inline, so the value can be corrected and retried.
  async function handleSave() {
    saveState = 'saving'
    saveError = null
    try {
      const result = await putSettings({ beat, arrhythmia })
      if (result.status === 'error') {
        saveError = result.error ?? 'Failed to save settings'
        return
      }
      onClose()
    } finally {
      saveState = 'idle'
    }
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- Escape is handled globally via the onMount keydown listener above; this
     backdrop only needs to catch a direct click on itself (not one bubbling
     from the card) to dismiss, so it is deliberately not a focusable control. -->
<div class="settings-backdrop" data-testid="settings-backdrop" onclick={handleBackdropClick}>
  <div class="settings-dialog" data-testid="settings-dialog" role="dialog" aria-modal="true" aria-label="Settings">
    <div class="settings-header">
      <h2>Settings</h2>
      <button
        type="button"
        class="icon-btn"
        data-testid="close-settings-button"
        onclick={onClose}
        aria-label="Close settings"
      >
        <Icon name="x" />
      </button>
    </div>

    <div class="settings-body">
      <fieldset>
        <legend>Appearance</legend>
        <div class="theme-options" role="radiogroup" aria-label="Theme">
          {#each THEME_OPTIONS as option (option.mode)}
            <button
              type="button"
              class="theme-option"
              class:active={themeState.mode === option.mode}
              data-testid={`theme-${option.mode}-button`}
              aria-pressed={themeState.mode === option.mode}
              onclick={() => setTheme(option.mode)}
            >
              <Icon name={option.icon} size={18} />
              <span>{option.label}</span>
            </button>
          {/each}
        </div>
      </fieldset>

      {#if loadState === 'loading'}
        <p data-testid="settings-loading" class="text-muted">Loading settings…</p>
      {:else if loadState === 'error'}
        <p data-testid="settings-load-error" class="text-danger">Failed to load settings: {loadError}</p>
      {:else}
        <fieldset>
          <legend>Beat detection</legend>

          <label class="field">
            Minimum RR interval (ms)
            <input data-testid="beat-min-rr-input" type="number" bind:value={beat.min_RR} />
          </label>

          <label class="field field-inline">
            <input data-testid="beat-ecg-invert-input" type="checkbox" bind:checked={beat.ecg_invert} />
            Invert ECG
          </label>

          <label class="field field-inline">
            <input
              data-testid="beat-auto-detect-invert-input"
              type="checkbox"
              bind:checked={beat.auto_detect_invert}
            />
            Auto-detect inversion
          </label>

          <label class="field field-inline">
            <input data-testid="beat-ecg-filter-input" type="checkbox" bind:checked={beat.ecg_filter} />
            Filter ECG
          </label>

          <label class="field">
            Filter order
            <input data-testid="beat-ecg-filt-order-input" type="number" bind:value={beat.ecg_filt_order} />
          </label>

          <label class="field">
            Filter cutoff (Hz)
            <input data-testid="beat-ecg-filt-cutoff-input" type="number" bind:value={beat.ecg_filt_cutoff} />
          </label>

          <label class="field">
            Absolute threshold
            <input data-testid="beat-abs-thresh-input" type="number" bind:value={beat.abs_thresh} />
          </label>

          <label class="field">
            Percentile threshold
            <input data-testid="beat-perc-thresh-input" type="number" bind:value={beat.perc_thresh} />
          </label>
        </fieldset>

        <fieldset>
          <legend>Arrhythmia detection</legend>

          <label class="field">
            Bradycardia absolute HR
            <input
              data-testid="arrhythmia-bradycardia-absolute-hr-input"
              type="number"
              bind:value={arrhythmia.bradycardia_absolute_hr}
            />
          </label>

          <label class="field">
            Tachycardia absolute HR
            <input
              data-testid="arrhythmia-tachycardia-absolute-hr-input"
              type="number"
              bind:value={arrhythmia.tachycardia_absolute_hr}
            />
          </label>

          <label class="field">
            Skipped beat multiple of RR
            <input
              data-testid="arrhythmia-skipped-beat-multiple-rr-input"
              type="number"
              bind:value={arrhythmia.skipped_beat_multiple_rr}
            />
          </label>

          <label class="field">
            Premature beat multiple of RR
            <input
              data-testid="arrhythmia-premature-beat-multiple-rr-input"
              type="number"
              bind:value={arrhythmia.premature_beat_multiple_rr}
            />
          </label>

          <label class="field">
            Window size
            <input data-testid="arrhythmia-window-size-input" type="number" bind:value={arrhythmia.window_size} />
          </label>

          <label class="field">
            Eps
            <input data-testid="arrhythmia-eps-input" type="number" bind:value={arrhythmia.eps} />
          </label>

          <label class="field">
            Minimum samples
            <input data-testid="arrhythmia-min-samples-input" type="number" bind:value={arrhythmia.min_samples} />
          </label>
        </fieldset>
      {/if}
    </div>

    {#if loadState === 'loaded'}
      <div class="settings-footer">
        {#if saveError}
          <p data-testid="settings-error" class="text-danger">{saveError}</p>
        {/if}
        <div class="settings-footer-actions">
          <button
            type="button"
            class="btn"
            data-testid="cancel-settings-button"
            disabled={saveState === 'saving'}
            onclick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            class="btn btn-primary"
            data-testid="save-settings-button"
            disabled={saveState === 'saving'}
            onclick={handleSave}
          >
            Save
          </button>
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .settings-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-5);
    z-index: 100;
  }

  .settings-dialog {
    width: 100%;
    max-width: 560px;
    max-height: 85vh;
    display: flex;
    flex-direction: column;
    background: var(--color-surface-raised);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
  }

  .settings-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--space-4) var(--space-5);
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .settings-header h2 {
    font-size: var(--font-size-lg);
    font-weight: 600;
  }

  .settings-body {
    padding: var(--space-4) var(--space-5);
    overflow-y: auto;
    flex: 1;
  }

  .settings-footer {
    padding: var(--space-4) var(--space-5);
    border-top: 1px solid var(--color-border);
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .settings-footer-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-3);
  }

  .theme-options {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .theme-option {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
  }

  .theme-option:hover {
    background: var(--color-surface-hover);
  }

  .theme-option.active {
    border-color: var(--color-accent);
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }
</style>
