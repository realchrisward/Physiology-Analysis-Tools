<script module lang="ts">
  // How to keep the current review when a report replaces it: a backup the
  // backend keeps for a few days, an .xlsx export, or nothing.
  export type BackupMode = 'keep' | 'export' | 'none'
</script>

<script lang="ts">
  import { onMount } from 'svelte'
  import Icon from '../shared/Icon.svelte'
  import { getFileStateSummary } from '../../lib/api/persistence'
  import type { FileStateSummaryResult } from '../../lib/api/types'
  import { fileNameOf, formatDate, formatTimeLeft } from '../../lib/format'
  import {
    BACKUP_DAY_OPTIONS,
    REPORT_LABEL_COLOR_VAR,
    REPORT_LABEL_NAME,
    REPORT_LABELS,
    reportMatch,
  } from '../../lib/reports'
  import { channelLabel, fileRegistry } from '../../lib/stores/fileRegistry.svelte'
  import { getReportSession } from '../../lib/stores/reports.svelte'

  // Asks what a report is for, right after it was read: COMPARE with this
  // recording's review (read-only, nothing changes) or REPLACE the review with
  // it. Replacing goes through a second step that says what will be lost, on
  // which channel the report's beats are saved, and how to keep the old
  // review (a timed backup, an .xlsx export, or nothing).
  //
  // The dialog only gathers the choice; the review screen carries it out
  // (`onCompare` / `onReplace`), since replacing also has to settle its own
  // state (an open beat, an unsaved channel) and reload afterwards.
  let {
    path,
    activeChannel,
    channels,
    initialStep = 'choose',
    blockedReason = null,
    onCompare,
    onCancel,
    onReplace,
  }: {
    path: string
    activeChannel: string
    channels: string[]
    // 'replace' when the technician asked to adopt a report they are already
    // comparing with ("Use as my review…").
    initialStep?: 'choose' | 'replace'
    // Why replacing has to wait (beat detection still running), if it does.
    blockedReason?: string | null
    onCompare: () => void
    onCancel: () => void
    // Resolves to an error to show, or null once the review was replaced.
    // `backupDays`: how long a backup ('keep') is kept.
    onReplace: (channel: string, backup: BackupMode, backupDays: number) => Promise<string | null>
  } = $props()

  const uid = $props.id()

  let session = $derived(getReportSession(path))
  let summary = $derived(session.summary)
  let match = $derived(reportMatch(session.navigation))
  let fileRow = $derived(fileRegistry.find((row) => row.path === path))

  // svelte-ignore state_referenced_locally
  let step: 'choose' | 'replace' = $state(initialStep)
  let choice = $state<'compare' | 'replace'>('compare')
  // svelte-ignore state_referenced_locally
  let channel: string = $state(activeChannel)
  let backup = $state<BackupMode>('keep')
  let backupDays = $state(7)
  let busy = $state(false)
  let error = $state<string | null>(null)

  // What is saved for this recording now: what replacing would discard.
  let saved = $state<FileStateSummaryResult | null>(null)
  let savedError = $state<string | null>(null)
  let savedLoading = $state(false)

  let dialogEl: HTMLDivElement | undefined = $state()
  let compareCard: HTMLInputElement | undefined = $state()

  let decisions = $derived(summary ? (summary.counts['2'] ?? 0) + (summary.counts['-1'] ?? 0) : 0)
  // Anything saved (beats, or bad-data marks drawn before any beat was saved)
  // is something replacing would lose, so it gets the backup choice.
  let hasSaved = $derived(saved?.status === 'ok' && saved.found && (saved.beats > 0 || saved.bad_data_marks > 0))
  // Until it is known what is saved, replacing could lose it without a backup.
  let savedKnown = $derived(saved?.status === 'ok')

  async function loadSaved() {
    savedLoading = true
    savedError = null
    const result = await getFileStateSummary(path)
    savedLoading = false
    if (result.status === 'ok') saved = result
    else savedError = result.error ?? 'Could not read what is saved for this recording.'
  }

  onMount(() => {
    if (step === 'replace') void loadSaved()
    // The safe choice is the default one.
    queueMicrotask(() => {
      if (step === 'choose') compareCard?.focus()
      else dialogEl?.focus()
    })

    function handleKeydown(event: KeyboardEvent) {
      // Another dialog on top (the uncertain-beat question while replacing)
      // owns the keyboard.
      const top = document.querySelectorAll('[role="dialog"]')
      if (top.length > 0 && top[top.length - 1] !== dialogEl) return
      if (event.key === 'Escape' && !busy) {
        event.preventDefault()
        event.stopPropagation()
        onCancel()
      } else if (event.key === 'Tab' && dialogEl) {
        // Keep Tab inside the dialog: the screen behind it must not be
        // operated while the choice is open.
        const focusable = Array.from(
          dialogEl.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)'),
        ).filter((element) => element.offsetParent !== null || element === document.activeElement)
        if (focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        const inside = dialogEl.contains(document.activeElement)
        if (event.shiftKey && (!inside || document.activeElement === first || document.activeElement === dialogEl)) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && (!inside || document.activeElement === last)) {
          event.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', handleKeydown, true)
    return () => document.removeEventListener('keydown', handleKeydown, true)
  })

  function handleBackdropClick(event: MouseEvent) {
    if (event.target === event.currentTarget && !busy) onCancel()
  }

  function proceed() {
    if (choice === 'compare') {
      onCompare()
      return
    }
    step = 'replace'
    error = null
    if (!saved && !savedLoading) void loadSaved()
    queueMicrotask(() => dialogEl?.focus())
  }

  async function replace() {
    if (busy || blockedReason || !savedKnown) return
    busy = true
    error = null
    try {
      // Without the choice on screen (nothing was saved when asked) a backup
      // is still asked for: something may have been saved since, and the
      // backend skips backing up an empty review.
      error = await onReplace(channel, hasSaved ? backup : 'keep', backupDays)
    } catch (err) {
      error = err instanceof Error ? err.message : String(err)
    } finally {
      busy = false
    }
  }

  function backupUntil(): string {
    return formatDate(new Date(Date.now() + backupDays * 86400000).toISOString())
  }

  function plural(n: number, one: string, many = `${one}s`): string {
    return `${n.toLocaleString()} ${n === 1 ? one : many}`
  }

  let replaceLabel = $derived(
    !hasSaved
      ? 'Use as my review'
      : backup === 'export'
        ? 'Save a copy, then replace'
        : backup === 'none'
          ? 'Replace without a copy'
          : 'Keep a backup and replace',
  )
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- Escape is handled by the keydown listener above; the backdrop only takes a
     direct click on itself as Cancel. -->
<div class="use-backdrop" data-testid="report-use-backdrop" onclick={handleBackdropClick}>
  <div
    class="use-dialog"
    data-testid="report-use-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby={`${uid}-title`}
    tabindex="-1"
    bind:this={dialogEl}
  >
    {#if !summary}
      <p class="text-muted">The report is no longer loaded.</p>
      <div class="footer">
        <button type="button" class="btn" onclick={onCancel}>Close</button>
      </div>
    {:else if step === 'choose'}
      <header class="head">
        <span class="head-icon"><Icon name="file-text" size={18} /></span>
        <div class="head-text">
          <h2 id={`${uid}-title`}>How do you want to use this report?</h2>
          <p class="file" title={summary.report_path} data-testid="report-use-filename">{summary.filename}</p>
        </div>
      </header>

      <div class="facts" data-testid="report-use-facts">
        <span class="fact"><strong>{summary.total_beats.toLocaleString()}</strong> beats</span>
        {#each REPORT_LABELS as label (label)}
          {@const n = summary.counts[String(label)] ?? 0}
          {#if n > 0 && label !== 0}
            <span class="fact" title={REPORT_LABEL_NAME[label]}>
              <span class="swatch" style:background={`var(${REPORT_LABEL_COLOR_VAR[label]})`} aria-hidden="true"
              ></span>
              <strong>{n.toLocaleString()}</strong>
              {REPORT_LABEL_NAME[label].toLowerCase()}
            </span>
          {/if}
        {/each}
        {#if summary.bad_data_marks.length > 0}
          <span class="fact"><strong>{summary.bad_data_marks.length}</strong> bad-data ranges</span>
        {/if}
        {#if match}
          <span class="fact" class:fact-warn={match.low} data-testid="report-use-match">
            <strong>{match.percent}%</strong> match beats detected here
          </span>
        {/if}
      </div>

      {#if summary.warnings.length > 0}
        <ul class="warnings" data-testid="report-use-warnings">
          {#each summary.warnings as warning, index (index)}
            <li><Icon name="alert-triangle" size={14} /><span>{warning}</span></li>
          {/each}
        </ul>
      {/if}

      <fieldset class="choices">
        <legend class="sr-only">Use the report to</legend>
        <label class="choice" class:selected={choice === 'compare'}>
          <input
            type="radio"
            name={`${uid}-choice`}
            value="compare"
            bind:group={choice}
            bind:this={compareCard}
            data-testid="report-use-compare-option"
          />
          <span class="choice-body">
            <span class="choice-title">Compare with my review</span>
            <span class="choice-desc">
              Show the report's labels alongside your review and step through where they disagree. Nothing in your
              review changes.
            </span>
          </span>
        </label>
        <label class="choice" class:selected={choice === 'replace'}>
          <input
            type="radio"
            name={`${uid}-choice`}
            value="replace"
            bind:group={choice}
            data-testid="report-use-replace-option"
          />
          <span class="choice-body">
            <span class="choice-title">Replace my review with this report</span>
            <span class="choice-desc">
              The report's beats, decisions and bad-data marks become this recording's review, for example to carry
              on an earlier review here. You can keep a backup of your review first.
            </span>
          </span>
        </label>
      </fieldset>

      <div class="footer">
        <button type="button" class="btn" data-testid="report-use-cancel" onclick={onCancel}>Cancel</button>
        <button type="button" class="btn btn-primary" data-testid="report-use-continue" onclick={proceed}>
          {choice === 'compare' ? 'Compare' : 'Continue…'}
        </button>
      </div>
    {:else}
      <header class="head">
        <span class="head-icon head-icon-warn"><Icon name="refresh-cw" size={18} /></span>
        <div class="head-text">
          <h2 id={`${uid}-title`}>Replace your review</h2>
          <p class="file" title={summary.report_path}>with {summary.filename}</p>
        </div>
      </header>

      <section class="changes" data-testid="report-use-changes">
        {#if savedLoading}
          <p class="text-muted">Checking what is saved for this recording…</p>
        {:else if savedError}
          <p class="text-danger" data-testid="report-use-saved-error">
            {savedError} Nothing can be replaced until this is known.
          </p>
          <button type="button" class="btn btn-sm retry" onclick={() => void loadSaved()}>Try again</button>
        {:else}
          <table class="compare" data-testid="report-use-compare-table">
            <thead>
              <tr>
                <th scope="col"><span class="sr-only">What</span></th>
                <th scope="col">Your review now</th>
                <th scope="col">After replacing</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">Beats</th>
                <td>{hasSaved && saved ? saved.beats.toLocaleString() : '—'}</td>
                <td><strong>{summary.total_beats.toLocaleString()}</strong></td>
              </tr>
              <tr>
                <th scope="row" title="Beats marked Arrhythmia or Not arrhythmia">Decisions</th>
                <td>{hasSaved && saved ? saved.decided.toLocaleString() : '—'}</td>
                <td><strong>{decisions.toLocaleString()}</strong></td>
              </tr>
              <tr>
                <th scope="row">Bad-data ranges</th>
                <td>{hasSaved && saved ? saved.bad_data_marks : '—'}</td>
                <td><strong>{summary.bad_data_marks.length}</strong></td>
              </tr>
              <tr>
                <th scope="row">Comes from</th>
                <td>
                  {#if !hasSaved}
                    nothing saved yet
                  {:else if saved?.source_report}
                    <span title={saved.source_report}>report {fileNameOf(saved.source_report)}</span>
                  {:else}
                    this app
                  {/if}
                </td>
                <td><span title={summary.report_path}>report {summary.filename}</span></td>
              </tr>
            </tbody>
          </table>
          {#if !hasSaved}
            <p class="text-muted nothing">This recording has no saved review yet, so nothing will be lost.</p>
          {/if}
        {/if}
      </section>

      {#if channels.length > 1}
        <label class="field">
          Put the report's beats on channel
          <select bind:value={channel} data-testid="report-use-channel" disabled={busy}>
            {#each channels as option (option)}
              {@const label = channelLabel(fileRow, option)}
              <option value={option}>{label === option ? option : `${label} (${option})`}</option>
            {/each}
          </select>
        </label>
      {/if}
      {#if match?.low}
        <p class="hint hint-warn" data-testid="report-use-low-match">
          <Icon name="alert-triangle" size={14} />
          Only {match.percent}% of the report's beats line up with beats detected on
          {channelLabel(fileRow, activeChannel)}. It may have been made on a different channel.
        </p>
      {/if}

      {#if hasSaved}
        <fieldset class="backup">
          <legend>Keep a copy of your review now?</legend>
          <label class="backup-option">
            <input type="radio" name={`${uid}-backup`} value="keep" bind:group={backup} disabled={busy}
              data-testid="report-use-backup-keep" />
            <span>
              <span class="backup-title backup-title-row">
                Keep a backup in the app for
                <select
                  bind:value={backupDays}
                  disabled={busy}
                  data-testid="report-use-backup-days"
                  aria-label="Keep the backup for"
                  onchange={() => (backup = 'keep')}
                >
                  {#each BACKUP_DAY_OPTIONS as option (option)}
                    <option value={option}>{option} days</option>
                  {/each}
                </select>
              </span>
              <span class="backup-desc">
                Restore it any time until <strong>{backupUntil()}</strong> with <em>Restore backup</em> in the top bar.
              </span>
            </span>
          </label>
          <label class="backup-option">
            <input type="radio" name={`${uid}-backup`} value="export" bind:group={backup} disabled={busy}
              data-testid="report-use-backup-export" />
            <span>
              <span class="backup-title">Save a copy as an .xlsx file</span>
              <span class="backup-desc">You choose where to save it; your review is replaced only once the file is written.</span>
            </span>
          </label>
          <label class="backup-option">
            <input type="radio" name={`${uid}-backup`} value="none" bind:group={backup} disabled={busy}
              data-testid="report-use-backup-none" />
            <span>
              <span class="backup-title">Don't keep a copy</span>
              <span class="backup-desc">Your review now can't be brought back.</span>
            </span>
          </label>
          {#if saved?.backup}
            <p class="hint text-muted" data-testid="report-use-existing-backup">
              The backup you already have (made {formatDate(saved.backup.created_at)},
              {formatTimeLeft(saved.backup.expires_at)}) is kept too.
            </p>
          {/if}
        </fieldset>
      {/if}

      <p class="hint text-muted">
        Running beat detection again later replaces the report's beats with newly detected ones; decisions are kept
        where the times match.
      </p>

      {#if blockedReason}
        <p class="hint hint-warn" data-testid="report-use-blocked">
          <Icon name="clock" size={14} />
          {blockedReason}
        </p>
      {/if}
      {#if error}
        <p class="banner banner-error" data-testid="report-use-error" role="alert">{error}</p>
      {/if}

      <div class="footer">
        {#if initialStep === 'choose'}
          <button type="button" class="btn btn-ghost back" disabled={busy} onclick={() => (step = 'choose')}>
            <Icon name="chevron-left" size={14} /> Back
          </button>
        {/if}
        <button type="button" class="btn" data-testid="report-use-cancel" disabled={busy} onclick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          class="btn"
          class:btn-danger={hasSaved}
          class:btn-primary={!hasSaved}
          data-testid="report-use-replace"
          disabled={busy || !savedKnown || !!blockedReason}
          onclick={replace}
        >
          {#if busy}<span class="spinner" aria-hidden="true"></span>{/if}
          {busy ? 'Replacing…' : replaceLabel}
        </button>
      </div>
    {/if}
  </div>
</div>

<style>
  .use-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-4);
    z-index: 100;
  }

  .use-dialog {
    width: min(540px, 100%);
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
    font-size: var(--font-size-sm);
    outline: none;
  }

  .head {
    display: flex;
    align-items: flex-start;
    gap: var(--space-3);
  }

  .head-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 36px;
    height: 36px;
    border-radius: var(--radius-md);
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }

  .head-icon-warn {
    background: color-mix(in srgb, var(--color-warning) 18%, transparent);
    color: var(--color-warning);
  }

  .head-text {
    min-width: 0;
  }

  .head h2 {
    margin: 0;
    font-size: var(--font-size-lg);
  }

  .file {
    margin: 2px 0 0;
    color: var(--color-text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .fact {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: 2px var(--space-2);
    border: 1px solid var(--color-border);
    border-radius: 999px;
    background: var(--color-surface);
    font-size: 0.8125rem;
  }

  .fact-warn {
    border-color: var(--color-warning);
    background: color-mix(in srgb, var(--color-warning) 14%, transparent);
  }

  .swatch {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }

  .warnings {
    list-style: none;
    margin: 0;
    padding: var(--space-2) var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--color-warning) 10%, transparent);
    font-size: 0.8125rem;
  }

  .warnings li {
    display: flex;
    gap: var(--space-2);
    align-items: flex-start;
  }

  .warnings li :global(svg) {
    flex-shrink: 0;
    margin-top: 2px;
    color: var(--color-warning);
  }

  .choices,
  .backup {
    margin: 0;
    padding: 0;
    border: none;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  .choice {
    display: flex;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-3);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    cursor: pointer;
    transition: border-color var(--transition-fast), background var(--transition-fast);
  }

  .choice:hover {
    border-color: var(--color-accent);
  }

  .choice.selected {
    border-color: var(--color-accent);
    background: var(--color-accent-soft);
  }

  .choice input,
  .backup-option input {
    margin-top: 3px;
    flex-shrink: 0;
  }

  .choice-body,
  .backup-option > span {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .choice-title,
  .backup-title {
    font-weight: 600;
  }

  .choice-desc,
  .backup-desc {
    color: var(--color-text-muted);
    font-size: 0.8125rem;
    line-height: 1.45;
  }

  .changes {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-3);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }

  .changes p {
    margin: 0;
  }

  .retry {
    align-self: flex-start;
  }

  .compare {
    width: 100%;
    border-collapse: collapse;
    font-variant-numeric: tabular-nums;
  }

  .compare th,
  .compare td {
    padding: 3px var(--space-2);
    text-align: left;
    vertical-align: top;
  }

  .compare thead th {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-muted);
    border-bottom: 1px solid var(--color-border);
  }

  .compare tbody th {
    font-weight: 500;
    color: var(--color-text-muted);
    white-space: nowrap;
  }

  .compare tbody td {
    overflow-wrap: anywhere;
  }

  .backup-title-row {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
  }

  .backup-title-row select {
    padding: 1px var(--space-1);
    font-size: var(--font-size-sm);
    font-weight: 400;
  }




  .backup legend {
    padding: 0;
    margin-bottom: var(--space-1);
  }

  .backup-option {
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    cursor: pointer;
  }

  .hint {
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    margin: 0;
    font-size: 0.8125rem;
  }

  .hint :global(svg) {
    flex-shrink: 0;
    margin-top: 2px;
  }

  .hint-warn {
    color: var(--color-text);
  }

  .hint-warn :global(svg) {
    color: var(--color-warning);
  }

  .footer {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-2);
    padding-top: var(--space-2);
  }

  .back {
    margin-right: auto;
  }

  .spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
