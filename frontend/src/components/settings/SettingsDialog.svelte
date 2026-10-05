<script lang="ts">
  import { onMount } from 'svelte'
  import {
    deleteDatabase,
    getDatabaseInfo,
    getDefaultSettings,
    getSettings,
    putSettings,
  } from '../../lib/api/settings'
  import type { ArrhythmiaSettingsModel, BeatSettingsModel, DatabaseInfoResult } from '../../lib/api/types'
  import { formatBytes } from '../../lib/format'
  import Icon from '../shared/Icon.svelte'
  import { setTheme, themeState, type ThemeMode } from '../../lib/stores/theme.svelte'

  let { onClose }: { onClose: () => void } = $props()

  const THEME_OPTIONS: { mode: ThemeMode; label: string; icon: string }[] = [
    { mode: 'system', label: 'System', icon: 'monitor' },
    { mode: 'light', label: 'Light', icon: 'sun' },
    { mode: 'dark', label: 'Dark', icon: 'moon' },
    { mode: 'bw', label: 'Black & White', icon: 'contrast' },
  ]

  type TabId = 'general' | 'appearance' | 'database'
  const TABS: { id: TabId; label: string; icon: string }[] = [
    { id: 'general', label: 'General', icon: 'settings' },
    { id: 'appearance', label: 'Appearance', icon: 'monitor' },
    { id: 'database', label: 'Database', icon: 'folder' },
  ]
  let activeTab: TabId = $state('general')

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

  // Database tab: where the review database lives and roughly what's in it.
  // Loaded alongside `beat`/`arrhythmia` on mount (independent request, same
  // "one clear state value" discipline) rather than lazily on first tab
  // visit - the Database tab is one click away and the request is cheap, so
  // there's no loading flash the first time it's opened.
  type DbInfoState = 'loading' | 'loaded' | 'error'
  let dbInfoState: DbInfoState = $state('loading')
  let dbInfoError: string | null = $state(null)
  let dbInfo: DatabaseInfoResult | null = $state(null)

  // Danger zone: a separate, nested confirm dialog (see the template below)
  // gates the actual delete behind an explicit second click, since this
  // action is irreversible and wipes every file's review progress at once.
  let showDeleteDbConfirm: boolean = $state(false)
  type DeleteDbState = 'idle' | 'deleting'
  let deleteDbState: DeleteDbState = $state('idle')
  let deleteDbError: string | null = $state(null)

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
    beat_window: 0,
    beat_window_bias: 0,
    beat_length: 0,
    kde_bandwidth: 0,
    min_rr: 0,
    max_rr: 0,
    eps_auto: true,
    eps_percentile: 0,
    eps: 0,
    min_samples: 0,
  })

  onMount(async () => {
    const result = await getSettings()
    if ('error' in result) {
      loadState = 'error'
      loadError = result.error
    } else {
      beat = { ...result.beat }
      arrhythmia = { ...result.arrhythmia }
      loadState = 'loaded'
    }
  })

  onMount(async () => {
    const result = await getDatabaseInfo()
    if (result.status === 'error') {
      dbInfoState = 'error'
      dbInfoError = result.error ?? 'Failed to load database info'
      return
    }
    dbInfo = result
    dbInfoState = 'loaded'
  })

  // Dismiss via Escape, in addition to the backdrop click and `×` button
  // below — all three call the same `onClose` prop. A separate, synchronous
  // `onMount` (not the async load ones above) so its returned function is
  // actually treated as unmount cleanup by Svelte.
  onMount(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      // The confirm dialog, when open, owns Escape itself - it should
      // dismiss just the confirmation, not the whole Settings dialog behind
      // it, and never while the delete request is actually in flight.
      if (showDeleteDbConfirm) {
        if (deleteDbState !== 'deleting') showDeleteDbConfirm = false
        return
      }
      onClose()
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
  // Fills the form with factory defaults WITHOUT applying them - the
  // technician still has to Save, and can Cancel to walk away unchanged.
  // Same semantics as the old PySide6 dialog's own Restore Defaults button.
  async function handleRestoreDefaults() {
    const result = await getDefaultSettings()
    if ('error' in result) {
      saveError = result.error
      return
    }
    beat = { ...result.beat }
    arrhythmia = { ...result.arrhythmia }
    saveError = null
  }

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

  function handleDeleteDatabaseClick() {
    deleteDbError = null
    showDeleteDbConfirm = true
  }

  function handleCancelDeleteDatabase() {
    if (deleteDbState === 'deleting') return
    showDeleteDbConfirm = false
  }

  async function handleConfirmDeleteDatabase() {
    deleteDbState = 'deleting'
    deleteDbError = null
    const result = await deleteDatabase()
    if (result.status === 'error') {
      deleteDbError = result.error ?? 'Failed to delete database'
      deleteDbState = 'idle'
      return
    }
    // Every file's beat/review/bad-data state the rest of this window holds
    // in memory (the review screen's loaded chart included) now refers to
    // rows that no longer exist. Rather than hunting down and invalidating
    // every one of those caches individually, a full reload guarantees the
    // whole app picks up the fresh, empty database with nothing stale left
    // behind - the only safe way to "start over" after wiping it.
    window.location.reload()
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

    <div class="settings-tabs" role="tablist" aria-label="Settings sections">
      {#each TABS as tab (tab.id)}
        <button
          type="button"
          class="settings-tab"
          class:active={activeTab === tab.id}
          role="tab"
          aria-selected={activeTab === tab.id}
          data-testid={`settings-tab-${tab.id}`}
          onclick={() => (activeTab = tab.id)}
        >
          <Icon name={tab.icon} size={16} />
          {tab.label}
        </button>
      {/each}
    </div>

    <div class="settings-body">
      {#if activeTab === 'general'}
        <div class="tab-panel" data-testid="settings-panel-general" role="tabpanel">
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

              <p class="field-group-note">
                Beat shape analysis — each beat is cut into an epoch sized from
                this recording's own dominant RR interval, then compared by shape.
              </p>

              <label class="field">
                Beat window (multiples of RR)
                <input data-testid="arrhythmia-beat-window-input" type="number" step="0.1" bind:value={arrhythmia.beat_window} />
              </label>

              <label class="field">
                Window bias (-1 before beat … 1 after)
                <input
                  data-testid="arrhythmia-beat-window-bias-input"
                  type="number"
                  step="0.1"
                  bind:value={arrhythmia.beat_window_bias}
                />
              </label>

              <label class="field">
                Epoch length (samples)
                <input data-testid="arrhythmia-beat-length-input" type="number" bind:value={arrhythmia.beat_length} />
              </label>

              <label class="field">
                RR distribution smoothing
                <input
                  data-testid="arrhythmia-kde-bandwidth-input"
                  type="number"
                  step="0.01"
                  bind:value={arrhythmia.kde_bandwidth}
                />
              </label>

              <label class="field">
                Shortest plausible RR (s)
                <input data-testid="arrhythmia-min-rr-input" type="number" step="0.01" bind:value={arrhythmia.min_rr} />
              </label>

              <label class="field">
                Longest plausible RR (s)
                <input data-testid="arrhythmia-max-rr-input" type="number" step="0.01" bind:value={arrhythmia.max_rr} />
              </label>

              <label class="field field-inline">
                <input data-testid="arrhythmia-eps-auto-input" type="checkbox" bind:checked={arrhythmia.eps_auto} />
                Scale cluster threshold to each recording (recommended)
              </label>

              {#if arrhythmia.eps_auto}
                <label class="field">
                  Outlier sensitivity (percentile)
                  <input
                    data-testid="arrhythmia-eps-percentile-input"
                    type="number"
                    bind:value={arrhythmia.eps_percentile}
                  />
                </label>
              {:else}
                <label class="field">
                  Cluster radius (eps)
                  <input data-testid="arrhythmia-eps-input" type="number" step="0.01" bind:value={arrhythmia.eps} />
                </label>
              {/if}

              <label class="field">
                Minimum samples per cluster
                <input data-testid="arrhythmia-min-samples-input" type="number" bind:value={arrhythmia.min_samples} />
              </label>
            </fieldset>
          {/if}
        </div>
      {:else if activeTab === 'appearance'}
        <div class="tab-panel" data-testid="settings-panel-appearance" role="tabpanel">
          <fieldset>
            <legend>Theme</legend>
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
        </div>
      {:else}
        <div class="tab-panel" data-testid="settings-panel-database" role="tabpanel">
          <fieldset>
            <legend>Storage location</legend>
            {#if dbInfoState === 'loading'}
              <p data-testid="database-info-loading" class="text-muted">Loading…</p>
            {:else if dbInfoState === 'error'}
              <p data-testid="database-info-error" class="text-danger">
                Failed to load database info: {dbInfoError}
              </p>
            {:else if dbInfo}
              <div class="field">
                <span id="database-path-label">Folder</span>
                <code class="path-value" data-testid="database-path" aria-labelledby="database-path-label"
                  >{dbInfo.directory}</code
                >
              </div>
              <p class="field-group-note">
                This app keeps its own review database separately from your
                recordings, in the folder above. For each imported file it
                stores: detected beats (timestamp, amplitude, heart rate),
                which channel you last reviewed it on, confirmed/rejected/
                reassigned arrhythmia decisions, beats you added or removed by
                hand, and any bad-data ranges you marked. Your original
                recording files are never modified or copied into it.
              </p>
              <dl class="db-stats">
                <dt>Files tracked</dt>
                <dd data-testid="database-file-count">{dbInfo.file_count}</dd>
                <dt>Beats stored</dt>
                <dd data-testid="database-beat-count">{dbInfo.beat_count}</dd>
                <dt>Database size</dt>
                <dd data-testid="database-size">{formatBytes(dbInfo.size_bytes ?? 0)}</dd>
              </dl>
            {/if}
          </fieldset>

          <fieldset class="danger-zone">
            <legend>Danger zone</legend>
            <p class="field-group-note">
              Permanently erases every saved beat detection, review decision,
              manual edit, and bad-data mark for every file. Your original
              recordings on disk are never touched — only this app's own saved
              review progress, which cannot be recovered afterwards.
            </p>
            <button
              type="button"
              class="btn btn-danger"
              data-testid="delete-database-button"
              onclick={handleDeleteDatabaseClick}
            >
              <Icon name="trash" size={14} /> Delete database
            </button>
          </fieldset>
        </div>
      {/if}
    </div>

    <div class="settings-footer">
      {#if saveError}
        <p data-testid="settings-error" class="text-danger">{saveError}</p>
      {/if}
      <div class="settings-footer-actions">
        {#if activeTab === 'general' && loadState === 'loaded'}
          <button
            type="button"
            class="btn"
            data-testid="restore-defaults-button"
            title="Fill the form with factory defaults (not saved until you press Save)"
            disabled={saveState === 'saving'}
            onclick={handleRestoreDefaults}
          >
            <Icon name="refresh-cw" size={14} /> Restore defaults
          </button>
        {/if}
        <span class="settings-footer-spacer"></span>
        <button
          type="button"
          class="btn"
          data-testid="cancel-settings-button"
          disabled={saveState === 'saving'}
          onclick={onClose}
        >
          Cancel
        </button>
        {#if activeTab === 'general' && loadState === 'loaded'}
          <button
            type="button"
            class="btn btn-primary"
            data-testid="save-settings-button"
            disabled={saveState === 'saving'}
            onclick={handleSave}
          >
            Save
          </button>
        {/if}
      </div>
    </div>
  </div>
</div>

{#if showDeleteDbConfirm}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="confirm-backdrop"
    data-testid="delete-database-confirm-backdrop"
    onclick={(event) => {
      if (event.target === event.currentTarget) handleCancelDeleteDatabase()
    }}
  >
    <div
      class="confirm-dialog"
      data-testid="delete-database-confirm-dialog"
      role="alertdialog"
      aria-modal="true"
      aria-label="Confirm database deletion"
    >
      <div class="confirm-header">
        <Icon name="alert-circle" size={20} />
        <h3>Delete the database?</h3>
      </div>
      <p>
        This permanently deletes <strong>every file's</strong> saved beat
        detections, confirmed/rejected review decisions, manually added or
        removed beats, and bad-data marks, then starts a brand-new, empty
        database in its place.
      </p>
      <p class="text-muted">
        Your original recording files are not touched or deleted — only this
        app's own review history. <strong>This cannot be undone.</strong>
      </p>
      {#if deleteDbError}
        <p data-testid="delete-database-error" class="text-danger">{deleteDbError}</p>
      {/if}
      <div class="confirm-footer">
        <button
          type="button"
          class="btn"
          data-testid="cancel-delete-database-button"
          disabled={deleteDbState === 'deleting'}
          onclick={handleCancelDeleteDatabase}
        >
          Cancel
        </button>
        <button
          type="button"
          class="btn btn-danger"
          data-testid="confirm-delete-database-button"
          disabled={deleteDbState === 'deleting'}
          onclick={handleConfirmDeleteDatabase}
        >
          {deleteDbState === 'deleting' ? 'Deleting…' : 'Yes, delete the database'}
        </button>
      </div>
    </div>
  </div>
{/if}

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

  .settings-tabs {
    display: flex;
    gap: var(--space-1);
    padding: 0 var(--space-3);
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .settings-tab {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-3);
    border: none;
    border-bottom: 2px solid transparent;
    background: transparent;
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    font-weight: 500;
    transition: color var(--transition-fast), border-color var(--transition-fast);
  }

  .settings-tab:hover {
    color: var(--color-text);
  }

  .settings-tab.active {
    color: var(--color-accent);
    border-bottom-color: var(--color-accent);
  }

  .settings-body {
    padding: var(--space-4) var(--space-5);
    overflow-y: auto;
    flex: 1;
  }

  .tab-panel {
    display: flex;
    flex-direction: column;
  }

  .settings-footer {
    padding: var(--space-4) var(--space-5);
    border-top: 1px solid var(--color-border);
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .settings-footer-spacer {
    flex: 1;
  }

  .field-group-note {
    grid-column: 1 / -1;
    margin: var(--space-2) 0 0;
    font-size: var(--font-size-sm);
    color: var(--color-text-muted, #5b6b7c);
  }

  .settings-footer-actions {
    display: flex;
    align-items: center;
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

  .path-value {
    display: block;
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: var(--font-size-sm);
    overflow-wrap: break-word;
    user-select: all;
  }

  .db-stats {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: var(--space-1) var(--space-3);
    margin: 0;
    font-size: var(--font-size-sm);
  }

  .db-stats dt {
    font-weight: 600;
    color: var(--color-text-muted);
  }

  .db-stats dd {
    margin: 0;
  }

  .danger-zone {
    border-color: var(--color-danger, #b91c1c);
  }

  .danger-zone legend {
    color: var(--color-danger, #b91c1c);
  }

  .confirm-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 110;
    padding: var(--space-4);
  }

  .confirm-dialog {
    width: min(440px, 100%);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--color-surface);
    border-radius: var(--radius-md, 8px);
    box-shadow: var(--shadow-lg);
  }

  .confirm-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--color-danger, #b91c1c);
  }

  .confirm-header h3 {
    margin: 0;
    font-size: var(--font-size-lg);
  }

  .confirm-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-3);
  }
</style>
