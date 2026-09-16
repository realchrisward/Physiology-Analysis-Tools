<script lang="ts">
  import { onMount } from 'svelte'
  import { getSettings, putSettings } from '../../lib/api/settings'
  import type { ArrhythmiaSettingsModel, BeatSettingsModel } from '../../lib/api/types'

  let { onClose }: { onClose: () => void } = $props()

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

<div data-testid="settings-dialog">
  <h2>Settings</h2>

  {#if loadState === 'loading'}
    <p data-testid="settings-loading">Loading settings…</p>
  {:else if loadState === 'error'}
    <p data-testid="settings-load-error">Failed to load settings: {loadError}</p>
  {:else}
    <fieldset>
      <legend>Beat detection</legend>

      <label>
        Minimum RR interval (ms)
        <input data-testid="beat-min-rr-input" type="number" bind:value={beat.min_RR} />
      </label>

      <label>
        <input data-testid="beat-ecg-invert-input" type="checkbox" bind:checked={beat.ecg_invert} />
        Invert ECG
      </label>

      <label>
        <input
          data-testid="beat-auto-detect-invert-input"
          type="checkbox"
          bind:checked={beat.auto_detect_invert}
        />
        Auto-detect inversion
      </label>

      <label>
        <input data-testid="beat-ecg-filter-input" type="checkbox" bind:checked={beat.ecg_filter} />
        Filter ECG
      </label>

      <label>
        Filter order
        <input data-testid="beat-ecg-filt-order-input" type="number" bind:value={beat.ecg_filt_order} />
      </label>

      <label>
        Filter cutoff (Hz)
        <input data-testid="beat-ecg-filt-cutoff-input" type="number" bind:value={beat.ecg_filt_cutoff} />
      </label>

      <label>
        Absolute threshold
        <input data-testid="beat-abs-thresh-input" type="number" bind:value={beat.abs_thresh} />
      </label>

      <label>
        Percentile threshold
        <input data-testid="beat-perc-thresh-input" type="number" bind:value={beat.perc_thresh} />
      </label>
    </fieldset>

    <fieldset>
      <legend>Arrhythmia detection</legend>

      <label>
        Bradycardia absolute HR
        <input
          data-testid="arrhythmia-bradycardia-absolute-hr-input"
          type="number"
          bind:value={arrhythmia.bradycardia_absolute_hr}
        />
      </label>

      <label>
        Tachycardia absolute HR
        <input
          data-testid="arrhythmia-tachycardia-absolute-hr-input"
          type="number"
          bind:value={arrhythmia.tachycardia_absolute_hr}
        />
      </label>

      <label>
        Skipped beat multiple of RR
        <input
          data-testid="arrhythmia-skipped-beat-multiple-rr-input"
          type="number"
          bind:value={arrhythmia.skipped_beat_multiple_rr}
        />
      </label>

      <label>
        Premature beat multiple of RR
        <input
          data-testid="arrhythmia-premature-beat-multiple-rr-input"
          type="number"
          bind:value={arrhythmia.premature_beat_multiple_rr}
        />
      </label>

      <label>
        Window size
        <input data-testid="arrhythmia-window-size-input" type="number" bind:value={arrhythmia.window_size} />
      </label>

      <label>
        Eps
        <input data-testid="arrhythmia-eps-input" type="number" bind:value={arrhythmia.eps} />
      </label>

      <label>
        Minimum samples
        <input data-testid="arrhythmia-min-samples-input" type="number" bind:value={arrhythmia.min_samples} />
      </label>
    </fieldset>

    {#if saveError}
      <p data-testid="settings-error">{saveError}</p>
    {/if}

    <button data-testid="save-settings-button" disabled={saveState === 'saving'} onclick={handleSave}>
      Save
    </button>
    <button data-testid="cancel-settings-button" disabled={saveState === 'saving'} onclick={onClose}>
      Cancel
    </button>
  {/if}
</div>
