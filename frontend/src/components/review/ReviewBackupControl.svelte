<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { getFileStateSummary } from '../../lib/api/persistence'
  import type { FileStateSummaryResult, ReviewBackupInfo } from '../../lib/api/types'
  import { formatDate, formatEditTime, formatRelativeTime, formatTimeLeft, fileNameOf } from '../../lib/format'
  import { BACKUP_DAY_OPTIONS } from '../../lib/reports'
  import Icon from '../shared/Icon.svelte'

  // The top bar's "Restore backup" / "Review from report" button: where the
  // recording's review came from, the backup kept of the review before it (when
  // it was made, how long it is kept, what it holds) and restoring it. Shown
  // only when there is something to say (a review taken from a report, or a
  // backup).
  let {
    path,
    sourceReport,
    sourceReportAt,
    backups,
    restoring = false,
    error = null,
    channelName = (channel: string) => channel,
    onRestore,
  }: {
    path: string
    // The report the review was taken from, and when (ISO), if it was.
    sourceReport: string | null
    sourceReportAt: string | null
    // Every backup of an earlier review that is kept, newest first.
    backups: ReviewBackupInfo[]
    restoring?: boolean
    error?: string | null
    // How a channel is shown (its custom name, if it has one).
    channelName?: (channel: string) => string
    // Restores backup `backupId`, keeping the review it swaps out for `days`.
    // Resolves to whether it was restored.
    onRestore: (days: number, backupId: number) => Promise<boolean>
  } = $props()

  const uid = $props.id()

  let open = $state(false)
  let confirming = $state(false)
  // The backup the panel shows (and restores): the newest unless another
  // one in the list was picked.
  let selectedId: number | null = $state(null)
  let latest = $derived(backups[0] ?? null)
  let backup = $derived(backups.find((item) => item.id === selectedId) ?? latest)
  let alignRight = $state(false)
  let days: number = $state(7)
  let current: FileStateSummaryResult | null = $state(null)
  let currentError: string | null = $state(null)
  let root: HTMLDivElement | undefined = $state()
  let panel: HTMLDivElement | undefined = $state()
  let trigger: HTMLButtonElement | undefined = $state()

  // "5 days left" stays true while the app is open.
  let now = $state(Date.now())
  onMount(() => {
    const timer = setInterval(() => (now = Date.now()), 60_000)
    return () => clearInterval(timer)
  })

  function plural(n: number, one: string, many = `${one}s`): string {
    return `${n.toLocaleString()} ${n === 1 ? one : many}`
  }

  function holds(counts: { beats: number; reviewed: number; decided: number; bad_data_marks: number }): string {
    return [
      plural(counts.beats, 'beat'),
      `${counts.reviewed.toLocaleString()} reviewed (${counts.decided.toLocaleString()} with a decision)`,
      plural(counts.bad_data_marks, 'bad-data range'),
    ].join(' · ')
  }

  // How much of its time the backup has left, for the meter.
  let leftShare = $derived.by(() => {
    if (!backup) return 0
    const start = new Date(backup.created_at).getTime()
    const end = new Date(backup.expires_at).getTime()
    if (!(end > start)) return 0
    return Math.min(1, Math.max(0, (end - now) / (end - start)))
  })

  let mainLabel = $derived(
    latest ? (backups.length > 1 ? `Restore backup (${backups.length})` : 'Restore backup') : 'Review from report',
  )
  let subLabel = $derived(
    latest
      ? `made ${formatDate(latest.created_at)} · ${formatTimeLeft(latest.expires_at, now)}`
      : sourceReport
        ? fileNameOf(sourceReport)
        : '',
  )
  let triggerTitle = $derived(
    latest
      ? `A backup of your earlier review, made ${formatEditTime(latest.created_at)}, is kept until ${formatDate(latest.expires_at)} (${formatTimeLeft(latest.expires_at, now)}). Click to see what it holds and to restore it.`
      : `Your review was taken from ${sourceReport ?? 'a report'}. Click for details.`,
  )

  // Why a backup was made, in a few words.
  function reasonText(item: ReviewBackupInfo): string {
    switch (item.reason) {
      case 'restore':
        return 'saved when you restored an earlier backup'
      case 'redetect':
        return 'saved when beats were detected again and some reviewed beats were not found'
      case 'channel':
        return 'saved when you switched to another channel'
      case 'fresh':
        return 'saved when you chose Start fresh'
      case 'earlier':
        return 'saved when you went back to the review of an earlier version of the file'
      default:
        return item.replaced_by
          ? `saved when ${fileNameOf(item.replaced_by)} replaced it`
          : 'saved before your review was replaced'
    }
  }

  function pick(id: number) {
    selectedId = id
    confirming = false
  }

  async function loadCurrent() {
    currentError = null
    const result = await getFileStateSummary(path)
    if (result.status === 'ok') current = result
    else currentError = result.error ?? 'Could not read the saved review.'
  }

  async function openPanel() {
    open = true
    confirming = false
    selectedId = null
    // The swapped-out review is kept as long as the backup being restored was.
    days = latest && BACKUP_DAY_OPTIONS.includes(latest.days) ? latest.days : 7
    void loadCurrent()
    await tick()
    const rect = panel?.getBoundingClientRect()
    alignRight = !!rect && rect.right > window.innerWidth - 8
  }

  function close(refocus: boolean) {
    if (!open) return
    open = false
    confirming = false
    if (refocus) trigger?.focus()
  }

  async function restore() {
    if (restoring || !backup) return
    const done = await onRestore(days, backup.id)
    if (done) close(false)
  }

  function onKeydown(event: KeyboardEvent) {
    if (open && event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      if (confirming) confirming = false
      else close(true)
    }
  }

  function onOutside(event: Event) {
    if (!open || !root) return
    if (!root.contains(event.target as Node)) close(false)
  }
</script>

<svelte:window onkeydowncapture={onKeydown} onpointerdowncapture={onOutside} />

{#if sourceReport || latest}
  <div class="rbc" bind:this={root}>
    <button
      type="button"
      class="btn btn-sm rbc-trigger"
      class:rbc-trigger--backup={!!latest}
      data-testid="review-backup-button"
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-controls={open ? `${uid}-panel` : undefined}
      title={triggerTitle}
      bind:this={trigger}
      onclick={() => (open ? close(true) : void openPanel())}
    >
      <Icon name={latest ? 'refresh-cw' : 'file-text'} size={14} />
      <span class="rbc-lines">
        <span class="rbc-main">{mainLabel}</span>
        <span class="rbc-sub" data-testid="review-backup-button-sub">{subLabel}</span>
      </span>
      <Icon name={open ? 'chevron-up' : 'chevron-down'} size={12} />
    </button>

    {#if open}
      <div
        class="rbc-panel"
        class:align-right={alignRight}
        id={`${uid}-panel`}
        role="dialog"
        aria-label="Your review and its backup"
        data-testid="review-backup-panel"
        bind:this={panel}
      >
        <section class="rbc-section" data-testid="review-backup-current">
          <h4>Your review now</h4>
          <dl>
            <dt>Comes from</dt>
            <dd>
              {#if sourceReport}
                report <strong title={sourceReport}>{fileNameOf(sourceReport)}</strong>
                <span class="rbc-path" title={sourceReport}>{sourceReport}</span>
              {:else}
                this app (detected and reviewed here)
              {/if}
            </dd>
            {#if sourceReport && sourceReportAt}
              <dt>Replaced on</dt>
              <dd>{formatEditTime(sourceReportAt)}</dd>
            {/if}
            <dt>Holds</dt>
            <dd data-testid="review-backup-current-counts">
              {#if current}
                {current.found ? holds(current) : 'nothing saved yet'}
              {:else if currentError}
                <span class="text-danger">{currentError}</span>
              {:else}
                <span class="text-muted">counting…</span>
              {/if}
            </dd>
          </dl>
        </section>

        <section class="rbc-section" data-testid="review-backup-details">
          <h4>{backups.length > 1 ? `Backups kept (${backups.length})` : 'Backup of your earlier review'}</h4>
          {#if backups.length > 1}
            <ul class="rbc-list" data-testid="review-backup-list">
              {#each backups as item, index (item.id)}
                <li>
                  <button
                    type="button"
                    class="rbc-item"
                    class:selected={backup?.id === item.id}
                    aria-pressed={backup?.id === item.id}
                    data-testid="review-backup-list-item"
                    onclick={() => pick(item.id)}
                  >
                    <span class="rbc-item-main">
                      {formatEditTime(item.created_at)}{index === 0 ? ' · newest' : ''}
                    </span>
                    <span class="rbc-item-sub">
                      {item.channel ? `${channelName(item.channel)} · ` : ''}{plural(item.decided, 'decision')} ·
                      {formatTimeLeft(item.expires_at, now)}
                    </span>
                  </button>
                </li>
              {/each}
            </ul>
          {/if}
          {#if backup}
            <dl>
              <dt>Made</dt>
              <dd>{formatEditTime(backup.created_at)} <span class="text-muted">({formatRelativeTime(new Date(backup.created_at).getTime(), now)})</span></dd>
              <dt>Kept until</dt>
              <dd>
                {formatDate(backup.expires_at)} ·
                <strong data-testid="review-backup-time-left">{formatTimeLeft(backup.expires_at, now)}</strong>
                <span class="text-muted">of {plural(backup.days, 'day')}</span>
                <span class="rbc-meter" aria-hidden="true"><span style:width={`${Math.round(leftShare * 100)}%`}></span></span>
              </dd>
              <dt>Why</dt>
              <dd data-testid="review-backup-reason">{reasonText(backup)}</dd>
              <dt>Holds</dt>
              <dd data-testid="review-backup-counts">
                {holds(backup)}{#if backup.channel}, on channel {channelName(backup.channel)}{/if}
                {#if backup.source_report}
                  <span class="rbc-note">That review came from report <span title={backup.source_report}>{fileNameOf(backup.source_report)}</span>.</span>
                {/if}
              </dd>
            </dl>

            {#if confirming}
              <div class="rbc-confirm" data-testid="review-backup-confirm" role="group" aria-label="Restore the backup">
                <p>
                  Restore the backup from {formatEditTime(backup.created_at)}? Your review now is swapped out, not lost:
                  it is kept as a backup for
                  <select bind:value={days} disabled={restoring} data-testid="review-backup-days" aria-label="Keep it for">
                    {#each BACKUP_DAY_OPTIONS as option (option)}
                      <option value={option}>{option} days</option>
                    {/each}
                  </select>
                  so you can switch back.
                </p>
                <div class="rbc-actions">
                  <button type="button" class="btn btn-sm" disabled={restoring} onclick={() => (confirming = false)}>
                    Cancel
                  </button>
                  <button
                    type="button"
                    class="btn btn-sm btn-primary"
                    data-testid="review-backup-restore"
                    disabled={restoring}
                    onclick={restore}
                  >
                    {#if restoring}<span class="spinner" aria-hidden="true"></span>{/if}
                    {restoring ? 'Restoring…' : 'Restore backup'}
                  </button>
                </div>
              </div>
            {:else}
              <button
                type="button"
                class="btn btn-sm btn-primary rbc-start"
                data-testid="review-backup-start-restore"
                onclick={() => (confirming = true)}
              >
                <Icon name="refresh-cw" size={12} /> Restore this backup…
              </button>
            {/if}
            {#if error}<p class="text-danger rbc-error" data-testid="review-backup-error">{error}</p>{/if}
          {:else}
            <p class="text-muted rbc-empty">
              No backup is kept for this recording. One is made when a report replaces your review (if you choose to
              keep one), when you switch channel or start fresh, and when beats detected again leave a reviewed beat
              behind.
            </p>
          {/if}
        </section>
      </div>
    {/if}
  </div>
{/if}

<style>
  .rbc {
    position: relative;
    display: inline-flex;
  }

  .rbc-trigger {
    gap: var(--space-2);
    padding-top: 2px;
    padding-bottom: 2px;
    max-width: 260px;
    text-align: left;
  }

  .rbc-trigger--backup {
    border-color: var(--color-accent);
  }

  .rbc-lines {
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.2;
  }

  .rbc-main {
    font-weight: 600;
    white-space: nowrap;
  }

  .rbc-sub {
    font-size: 0.7rem;
    color: var(--color-text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .rbc-panel {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    z-index: 60;
    width: min(400px, calc(100vw - 16px));
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-3);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-raised);
    box-shadow: var(--shadow-md);
    font-size: var(--font-size-sm);
    color: var(--color-text);
  }

  .rbc-panel.align-right {
    left: auto;
    right: 0;
  }

  .rbc-section h4 {
    margin: 0 0 var(--space-2);
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-muted);
  }

  .rbc-section + .rbc-section {
    padding-top: var(--space-3);
    border-top: 1px solid var(--color-border);
  }

  dl {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: var(--space-1) var(--space-3);
    margin: 0;
  }

  dt {
    color: var(--color-text-muted);
  }

  dd {
    margin: 0;
    min-width: 0;
  }

  .rbc-path {
    display: block;
    font-size: 0.75rem;
    color: var(--color-text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .rbc-meter {
    display: block;
    height: 4px;
    margin-top: 4px;
    border-radius: 999px;
    background: var(--color-border);
    overflow: hidden;
  }

  .rbc-meter > span {
    display: block;
    height: 100%;
    background: var(--color-accent);
  }

  .rbc-note {
    display: block;
    margin: var(--space-1) 0 0;
    font-size: 0.75rem;
    color: var(--color-text-muted);
  }

  .rbc-list {
    list-style: none;
    margin: 0 0 var(--space-2);
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 168px;
    overflow-y: auto;
  }

  .rbc-item {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 1px;
    padding: var(--space-1) var(--space-2);
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--color-text);
    font-size: var(--font-size-sm);
    text-align: left;
  }

  .rbc-item:hover {
    background: var(--color-surface-hover);
  }

  .rbc-item.selected {
    border-color: var(--color-accent);
    background: var(--color-accent-soft);
  }

  .rbc-item-main {
    font-weight: 500;
  }

  .rbc-item-sub {
    font-size: 0.75rem;
    color: var(--color-text-muted);
  }

  .rbc-start {
    margin-top: var(--space-3);
    gap: var(--space-1);
  }

  .rbc-confirm {
    margin-top: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-accent);
    border-radius: var(--radius-sm);
    background: var(--color-accent-soft);
  }

  .rbc-confirm p {
    margin: 0;
    line-height: 1.5;
  }

  .rbc-confirm select {
    padding: 1px var(--space-1);
    font-size: var(--font-size-sm);
  }

  .rbc-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-2);
    margin-top: var(--space-2);
  }

  .rbc-error,
  .rbc-empty {
    margin: var(--space-2) 0 0;
  }

  .spinner {
    display: inline-block;
    width: 10px;
    height: 10px;
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
