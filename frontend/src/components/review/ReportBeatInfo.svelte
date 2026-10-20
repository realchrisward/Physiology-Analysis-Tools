<script lang="ts">
  import { getReportBeat } from '../../lib/api/reports'
  import {
    REPORT_AGREEMENT_TEXT,
    REPORT_LABEL_COLOR_VAR,
    REPORT_LABEL_NAME,
    reportCategoryName,
  } from '../../lib/reports'
  import type { ReportBeat } from '../../lib/api/types'

  // What the loaded report says about the selected beat — shown above the
  // beat's own review controls, so the two can be compared at a glance. Only
  // ever rendered while a report is attached (ReviewWorkspace's `{#if}`).
  let {
    path,
    ts,
    refreshToken = 0,
  }: {
    path: string
    // The selected beat's timestamp (seconds).
    ts: number
    // Bumped by the parent when OUR beats changed (a confirm, a reject, a new
    // detection...), which can change whether the two sides agree.
    refreshToken?: number
  } = $props()

  // The answer is stored WITH the timestamp it was asked for, and only shown
  // while that is still the selected beat: a slow response for the previous
  // selection can neither be applied (see `requestId`) nor linger on screen
  // under the new one while the next request is in flight.
  interface Answer {
    ts: number
    beat: ReportBeat | null
    error: string | null
  }
  let answer = $state<Answer | null>(null)
  let requestId = 0

  $effect(() => {
    const forTs = ts
    // Read so a bump re-runs the lookup for the SAME beat.
    refreshToken
    const id = ++requestId
    void getReportBeat(path, forTs).then((result) => {
      if (id !== requestId) return // a newer selection or refresh took over, or this unmounted
      if (result.status === 'ok' && !result.error) {
        answer = { ts: forTs, beat: result.beat ?? null, error: null }
      } else {
        answer = { ts: forTs, beat: null, error: result.error ?? 'Could not read the report' }
      }
    })
    return () => {
      // Invalidates the request above when `ts`/`refreshToken` change or the
      // component goes away.
      requestId++
    }
  })

  let current = $derived(answer && answer.ts === ts ? answer : null)
  let beat = $derived(current?.beat ?? null)
  let flaggedBy = $derived(beat ? beat.categories.map(reportCategoryName).join(', ') : '')
  // A disagreement is the point of comparing, so it stands out from the rest.
  let disagrees = $derived(beat?.agreement === 'report_only' || beat?.agreement === 'ours_only')
</script>

<div class="report-beat-info" data-testid="report-beat-info" role="region" aria-label="Report label for this beat">
  {#if current === null}
    <p class="text-muted line" data-testid="report-beat-loading">Report: checking…</p>
  {:else if current.error}
    <p class="text-muted line" data-testid="report-beat-error">Report: {current.error}</p>
  {:else if beat === null}
    <p class="text-muted line" data-testid="report-beat-absent">Not in the report</p>
  {:else}
    <p class="line" data-testid="report-beat-label">
      <span class="prefix">Report:</span>
      <span class="label" style:color={`var(${REPORT_LABEL_COLOR_VAR[beat.label]})`}>
        {REPORT_LABEL_NAME[beat.label]}
      </span>
      {#if flaggedBy}<span class="text-muted">· flagged by {flaggedBy}</span>{/if}
    </p>
    <p
      class="line agreement"
      class:disagrees
      class:text-muted={!disagrees}
      data-testid="report-beat-agreement"
    >
      {REPORT_AGREEMENT_TEXT[beat.agreement]}
    </p>
  {/if}
</div>

<style>
  .report-beat-info {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-2) var(--space-3);
    margin-bottom: var(--space-3);
    border: 1px solid var(--color-border);
    border-left-width: 3px;
    border-radius: var(--radius-sm);
    background: var(--color-surface);
    font-size: var(--font-size-sm);
  }

  .line {
    line-height: 1.35;
  }

  .prefix {
    color: var(--color-text-muted);
  }

  .label {
    font-weight: 600;
  }

  .agreement {
    font-size: 0.8125rem;
  }

  .agreement.disagrees {
    padding: 2px var(--space-2);
    margin: 0 calc(-1 * var(--space-2));
    border-radius: var(--radius-sm);
    background: var(--color-report-disagree-soft);
    color: var(--color-text);
    font-weight: 600;
  }
</style>
