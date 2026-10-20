<script lang="ts">
  import Icon from '../shared/Icon.svelte'
  import {
    attachReport,
    detachReport,
    dismissReportError,
    getReportLink,
    getReportSession,
    refreshReportSummary,
    reportFolders,
  } from '../../lib/stores/reports.svelte'
  import { REPORT_LABEL_COLOR_VAR, REPORT_LABEL_NAME, REPORT_LABEL_TITLE, REPORT_LABELS } from '../../lib/reports'

  // The toolbar's "Report labels" control: load an xlsx report whose per-beat
  // labels are shown over the recording, see what was loaded and how well it
  // matches the beats detected here, replace it or remove it. All state is the
  // shared `reports` store's (keyed by recording path), so this component only
  // renders it and issues the actions.
  let { path }: { path: string } = $props()

  const uid = $props.id()

  let session = $derived(getReportSession(path))
  let link = $derived(getReportLink(path))
  let summary = $derived(session.summary)
  let navigation = $derived(session.navigation)

  let open = $state(false)
  // Opening is only meaningful while a report is attached.
  let popoverOpen = $derived(open && session.status === 'ready' && summary !== null)
  let alignRight = $state(false)
  let picking = false
  let note: string | null = $state(null)

  let root: HTMLElement | undefined = $state()
  let chipButton: HTMLButtonElement | undefined = $state()

  // Chip counts: the labels a reviewer cares about, in the report's own
  // vocabulary. Label 0 (normal) is the bulk of every report and stays in the
  // popover.
  const CHIP_PARTS = [
    { key: '2', label: 'arr', color: REPORT_LABEL_COLOR_VAR[2] },
    { key: '1', label: 'not reviewed', color: REPORT_LABEL_COLOR_VAR[1] },
    { key: '-1', label: 'rejected', color: REPORT_LABEL_COLOR_VAR[-1] },
  ] as const

  let countParts = $derived(
    summary
      ? CHIP_PARTS.map((part) => ({ ...part, n: summary.counts[part.key] ?? 0 })).filter((part) => part.n > 0)
      : [],
  )

  // How many of the report's beats line up with a beat detected HERE. Hidden
  // until our beats exist: before that nothing can match and "0%" would only
  // look like a problem.
  let match = $derived.by(() => {
    if (!navigation || navigation.status !== 'ok' || !navigation.has_our_beats) return null
    const total = navigation.total ?? 0
    if (total <= 0) return null
    const matched = navigation.matched ?? 0
    // Never round up to 100% while a beat is unmatched.
    const percent = matched >= total ? 100 : Math.min(99, Math.floor((matched / total) * 100))
    const notDetected = navigation.targets?.not_detected?.length ?? total - matched
    return { matched, total, percent, notDetected, extra: navigation.extra_ours ?? 0, low: matched / total < 0.9 }
  })

  let matchTitle = $derived.by(() => {
    if (!match) return ''
    const base = `${match.matched} of ${match.total} report beats match a beat detected in this recording.`
    if (!match.low) return base
    const names = (summary?.settings_differences ?? []).map((difference) => difference.name)
    return (
      `${base} A low match usually means the report was made on a different channel or with different ` +
      'beat-detection settings' +
      (names.length > 0 ? ` (settings that differ: ${names.join(', ')})` : '') +
      '.'
    )
  })

  let warnings = $derived(summary?.warnings ?? [])
  let differences = $derived(summary?.settings_differences ?? [])

  async function choose() {
    open = false
    if (picking) return
    // The web build / tests have no Electron bridge, so there is no picker.
    const pick = window.api?.pickReport
    if (!pick) {
      note = 'Choosing a report file needs the desktop app. You can also drop an .xlsx file here.'
      return
    }
    picking = true
    note = null
    try {
      const picked = await window.api?.pickReport?.(reportFolders[0])
      if (picked) void attachReport(path, picked)
    } catch (err) {
      note = `Could not open the file picker: ${err instanceof Error ? err.message : String(err)}`
    } finally {
      picking = false
    }
  }

  function retry() {
    const target = session.reportPath ?? link
    if (target) void attachReport(path, target)
    else void choose()
  }

  function remove() {
    open = false
    void detachReport(path)
  }

  function toggle() {
    if (open) {
      open = false
      return
    }
    const rect = chipButton?.getBoundingClientRect()
    // The control sits at the toolbar's right end; keep the popover on screen.
    alignRight = !!rect && rect.left + 400 > window.innerWidth
    open = true
    void refreshReportSummary(path)
  }

  function onKeydown(event: KeyboardEvent) {
    if (!popoverOpen || event.key !== 'Escape') return
    // Closing the popover is all this Escape means: the graph must not also
    // act on it (leave add-beat mode, clear the selection...).
    event.stopPropagation()
    event.preventDefault()
    open = false
    chipButton?.focus()
  }

  function onOutside(event: Event) {
    if (!popoverOpen || !root) return
    if (event.target instanceof Node && root.contains(event.target)) return
    open = false
  }
</script>

<svelte:window onkeydowncapture={onKeydown} onpointerdowncapture={onOutside} onclickcapture={onOutside} />

<div class="report-control" data-testid="report-control" bind:this={root}>
  {#if session.status === 'loading'}
    <span class="report-loading text-muted" data-testid="report-loading" role="status">
      <span class="spinner" aria-hidden="true"></span>
      Reading report…
    </span>
  {:else if session.status === 'ready' && summary}
    <div class="chip-wrap">
      <button
        type="button"
        class="report-chip"
        class:open={popoverOpen}
        data-testid="report-chip"
        aria-expanded={popoverOpen}
        aria-controls={`${uid}-details`}
        aria-label={`Report ${summary.filename}, show details`}
        bind:this={chipButton}
        onclick={toggle}
      >
        <Icon name="file-text" size={14} />
        <span class="chip-name" data-testid="report-chip-name" title={summary.report_path}>{summary.filename}</span>
        <span class="chip-counts" data-testid="report-chip-counts">
          {#each countParts as part, index (part.key)}
            {#if index > 0}<span class="sep" aria-hidden="true">·</span>{/if}
            <span style:color={`var(${part.color})`}>{part.n} {part.label}</span>
          {:else}
            <span class="text-muted">{summary.total_beats} beats</span>
          {/each}
        </span>
        {#if match}
          <span class="pill" class:pill-low={match.low} data-testid="report-match-pill" title={matchTitle}>
            {match.percent}% matched
          </span>
        {/if}
        {#if warnings.length > 0}
          <span
            class="chip-warning"
            data-testid="report-warning-icon"
            title={warnings.join('\n')}
            role="img"
            aria-label={`${warnings.length} ${warnings.length === 1 ? 'warning' : 'warnings'} about this report`}
          >
            <Icon name="alert-triangle" size={14} />
          </span>
        {/if}
        <Icon name="chevron-down" size={12} />
      </button>

      {#if popoverOpen}
        <div
          class="details"
          class:align-right={alignRight}
          id={`${uid}-details`}
          data-testid="report-details"
          role="region"
          aria-label="Report details"
        >
          <dl class="meta">
            <dt>File</dt>
            <dd class="path" data-testid="report-details-path">{summary.report_path}</dd>
            <dt>Sheet</dt>
            <dd data-testid="report-details-sheet">{summary.sheet} · label column <code>{summary.label_column}</code></dd>
          </dl>

          <h4>Beats in the report <span class="text-muted">({summary.total_beats})</span></h4>
          <ul class="totals" data-testid="report-details-totals">
            {#each REPORT_LABELS as label (label)}
              <li title={REPORT_LABEL_TITLE[label]}>
                <span class="swatch" style:background={`var(${REPORT_LABEL_COLOR_VAR[label]})`} aria-hidden="true"
                ></span>
                <span class="total-name">{REPORT_LABEL_NAME[label]}</span>
                <strong>{summary.counts[String(label)] ?? 0}</strong>
              </li>
            {/each}
          </ul>
          {#if summary.skipped_rows > 0}
            <p class="text-muted small" data-testid="report-details-skipped">
              {summary.skipped_rows} {summary.skipped_rows === 1 ? 'row was' : 'rows were'} skipped (unreadable time
              or label).
            </p>
          {/if}

          <h4>Match with this recording</h4>
          {#if match}
            <ul class="totals" data-testid="report-details-match">
              <li><span class="total-name">Matched to a beat detected here</span><strong>{match.matched}</strong></li>
              <li><span class="total-name">Not detected by us</span><strong>{match.notDetected}</strong></li>
              <li><span class="total-name">Extra beats of ours</span><strong>{match.extra}</strong></li>
            </ul>
          {:else}
            <p class="text-muted small" data-testid="report-details-nomatch">
              Beats have not been detected for this recording yet, so nothing can be matched.
            </p>
          {/if}

          {#if differences.length > 0}
            <h4>Detection settings that differ</h4>
            <table class="diff" data-testid="report-details-differences">
              <thead>
                <tr><th scope="col">Setting</th><th scope="col">Report</th><th scope="col">Ours</th></tr>
              </thead>
              <tbody>
                {#each differences as difference (difference.name)}
                  <tr>
                    <th scope="row">{difference.name}</th>
                    <td>{difference.report_value}</td>
                    <td>{difference.our_value}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}

          {#if warnings.length > 0}
            <h4>Warnings</h4>
            <ul class="warnings" data-testid="report-details-warnings">
              {#each warnings as warning, index (index)}
                <li>{warning}</li>
              {/each}
            </ul>
          {/if}

          <div class="actions">
            <button type="button" class="btn btn-sm" data-testid="report-replace-button" onclick={choose}>
              Replace…
            </button>
            <button
              type="button"
              class="btn btn-sm btn-danger-text"
              data-testid="report-remove-button"
              onclick={remove}
            >
              Remove
            </button>
          </div>
        </div>
      {/if}
    </div>

    {#if session.error}
      <span class="replace-error text-danger" data-testid="report-replace-error" title={session.error}>
        <Icon name="alert-circle" size={14} />
        <span class="replace-error-text">{session.error}</span>
        <button
          type="button"
          class="icon-btn dismiss"
          data-testid="report-replace-error-dismiss"
          aria-label="Dismiss"
          onclick={() => dismissReportError(path)}
        >
          <Icon name="x" size={12} />
        </button>
      </span>
    {/if}
  {:else if session.status === 'error' || session.status === 'missing'}
    <div class="report-chip report-chip-error" data-testid="report-error" data-status={session.status}>
      <Icon name="alert-circle" size={14} />
      <span class="error-text" title={session.error ?? undefined}>
        {session.status === 'missing' ? 'Linked report unavailable' : 'Report not loaded'}:
        {session.error ?? 'unknown error'}
      </span>
      <button type="button" class="btn btn-sm" data-testid="report-retry-button" onclick={retry}>Retry</button>
      <button type="button" class="btn btn-sm" data-testid="report-choose-button" onclick={choose}>
        Choose file…
      </button>
      <button type="button" class="btn btn-sm btn-danger-text" data-testid="report-remove-button" onclick={remove}>
        {link ? 'Remove link' : 'Dismiss'}
      </button>
    </div>
  {:else}
    <button
      type="button"
      class="btn btn-sm"
      data-testid="load-report-button"
      title="Load an xlsx report with per-beat labels (2 arrhythmia, 1 not reviewed, 0 normal, -1 rejected) to compare with this recording"
      onclick={choose}
    >
      Load report…
    </button>
  {/if}

  {#if note}
    <span class="note text-muted" data-testid="report-picker-note">
      {note}
      <button
        type="button"
        class="icon-btn dismiss"
        aria-label="Dismiss"
        onclick={() => (note = null)}
      >
        <Icon name="x" size={12} />
      </button>
    </span>
  {/if}
</div>

<style>
  .report-control {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-2);
    min-width: 0;
  }

  .chip-wrap {
    position: relative;
    min-width: 0;
  }

  .report-loading {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--font-size-sm);
  }

  .report-chip {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    max-width: 100%;
    padding: var(--space-1) var(--space-2);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    color: var(--color-text);
    font-size: var(--font-size-sm);
    line-height: 1.2;
    text-align: left;
    transition: background var(--transition-fast), border-color var(--transition-fast);
  }

  button.report-chip:hover,
  button.report-chip.open {
    background: var(--color-surface-hover);
  }

  button.report-chip.open {
    border-color: var(--color-accent);
  }

  .chip-name {
    max-width: 160px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
  }

  .chip-counts {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    white-space: nowrap;
    font-size: 0.8125rem;
  }

  .sep {
    color: var(--color-text-muted);
  }

  .pill {
    padding: 1px var(--space-2);
    border-radius: 999px;
    background: var(--color-accent-soft);
    color: var(--color-text);
    font-size: 0.75rem;
    font-weight: 600;
    white-space: nowrap;
  }

  /* Amber: most of the report's beats have no beat of ours beside them. The
     text stays the normal colour so it reads in every theme, including the
     black & white one where the warning colour is plain black. */
  .pill-low {
    background: color-mix(in srgb, var(--color-warning) 18%, transparent);
    border: 1px solid var(--color-warning);
    padding: 0 var(--space-2);
  }

  .chip-warning {
    display: inline-flex;
    color: var(--color-warning);
  }

  .report-chip-error {
    flex-wrap: wrap;
    border-color: var(--color-danger);
    background: color-mix(in srgb, var(--color-danger) 10%, var(--color-surface));
    color: var(--color-danger);
  }

  .error-text {
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .replace-error {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    max-width: 280px;
    font-size: 0.8125rem;
  }

  .replace-error-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .dismiss {
    width: 20px;
    height: 20px;
    color: inherit;
  }

  .note {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    font-size: 0.8125rem;
  }

  .details {
    position: absolute;
    top: calc(100% + var(--space-1));
    left: 0;
    z-index: 40;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    width: 380px;
    max-width: calc(100vw - 2 * var(--space-4));
    max-height: min(70vh, 560px);
    overflow-y: auto;
    padding: var(--space-3);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-raised);
    box-shadow: var(--shadow-md);
    font-size: var(--font-size-sm);
  }

  .details.align-right {
    left: auto;
    right: 0;
  }

  .details h4 {
    margin: var(--space-1) 0 0;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-muted);
  }

  .meta {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 2px var(--space-3);
    margin: 0;
  }

  .meta dt {
    color: var(--color-text-muted);
  }

  .meta dd {
    margin: 0;
    min-width: 0;
  }

  .path {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    overflow-wrap: anywhere;
  }

  .totals {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .totals li {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .total-name {
    flex: 1;
  }

  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .small {
    font-size: 0.8125rem;
  }

  .diff {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8125rem;
  }

  .diff th,
  .diff td {
    padding: 2px var(--space-2);
    border-bottom: 1px solid var(--color-border);
    text-align: left;
    overflow-wrap: anywhere;
  }

  .diff thead th {
    color: var(--color-text-muted);
    font-weight: 600;
  }

  .diff tbody th {
    font-weight: 500;
  }

  .warnings {
    margin: 0;
    padding-left: var(--space-4);
    color: var(--color-warning);
    font-size: 0.8125rem;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-2);
    padding-top: var(--space-2);
    border-top: 1px solid var(--color-border);
  }

  .spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid var(--color-border);
    border-top-color: var(--color-accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
