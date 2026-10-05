<script lang="ts">
  import { detectArrhythmias, type ArrhythmiaMethod } from '../../lib/api/arrhythmia'
  import Icon from '../shared/Icon.svelte'

  let {
    path,
    channel,
    onComplete,
    disabled = false,
    done = [],
  }: {
    path: string
    channel: string
    onComplete: (method: ArrhythmiaMethod) => void
    // Set by ReviewWorkspace to `!hydrationChecked` — see that component's
    // comment on the `<ArrhythmiaControls>` usage. ANDed with this
    // component's own `runState`-based button-disabling below rather than
    // replacing it, so a run already in flight still keeps the buttons
    // disabled even after `disabled` itself later flips back to `false`.
    // Defaults to `false` so every other caller/test that doesn't pass it
    // keeps today's behavior unchanged.
    disabled?: boolean
    // Methods that have already produced results for this channel. Owned by
    // ReviewWorkspace rather than tracked here because its automatic on-load
    // run counts too. A done method's button turns green with a check but
    // stays clickable, so it can be run again (e.g. after changing settings).
    done?: ArrhythmiaMethod[]
  } = $props()

  const METHODS: { id: ArrhythmiaMethod; label: string }[] = [
    { id: 'heuristic', label: 'Run heuristic' },
    { id: 'unsupervised', label: 'Run unsupervised' },
  ]

  // Single state value for the whole re-run lifecycle, set synchronously
  // before the first `await` in `run` below — the same "one clear state
  // value, not multiple booleans that can drift out of sync" discipline
  // ImportScreen's `queueState` established (and ReviewWorkspace's own
  // `detectionRequestId`/`persistRequestId` guards reuse), rather than a
  // separate `running`/`disabled` pair.
  //   'idle'    - no re-run in flight; both method buttons enabled.
  //   'running' - a re-run is in flight; both method buttons disabled
  //               and a progress indicator is shown.
  type RunState = 'idle' | 'running'
  let runState: RunState = $state('idle')
  let error: string | null = $state(null)
  // Which specific method is running, so its own button can show a spinner
  // in place of the other — distinct from `runState`, which just gates
  // whether BOTH buttons are disabled together.
  let runningMethod: ArrhythmiaMethod | null = $state(null)

  async function run(method: ArrhythmiaMethod) {
    // Captured up front: the channel this specific run is FOR. `channel` is
    // a live prop (this component is mounted as `channel={activeChannel}`
    // in ReviewWorkspace, not remounted/reset via `{#key activeChannel}`),
    // so if the technician switches channels while `detectArrhythmias` below
    // is still in flight, `channel` will have already moved on by the time
    // it resolves — re-reading it post-`await` gives the CURRENT channel,
    // not the one this run was dispatched for.
    const requestedChannel = channel
    runState = 'running'
    runningMethod = method
    error = null
    try {
      const result = await detectArrhythmias(path, requestedChannel, method)
      if (requestedChannel !== channel) return // superseded by a channel switch while this run was in flight

      if (result.status === 'ok' && !result.error) {
        // Only a genuinely successful run tells ReviewWorkspace to refresh
        // EcgGraph's beat markers — a failed/unchanged result must not
        // trigger a refetch against data that hasn't actually changed.
        onComplete(method)
      } else {
        error = result.error ?? 'Arrhythmia detection failed'
      }
    } finally {
      runState = 'idle'
      runningMethod = null
    }
  }
</script>

<div class="arrhythmia-controls" data-testid="arrhythmia-controls">
  <span class="arrhythmia-label text-muted">Arrhythmia detection</span>
  {#each METHODS as method (method.id)}
    {@const isDone = done.includes(method.id)}
    <button
      type="button"
      class="btn btn-sm"
      class:btn-done={isDone}
      data-testid={`run-${method.id}-button`}
      disabled={runState === 'running' || disabled}
      title={isDone ? 'Done — click to run again' : undefined}
      aria-label={isDone ? `${method.label}, done` : undefined}
      onclick={() => run(method.id)}
    >
      {#if runningMethod === method.id}
        <span class="spinner" aria-hidden="true"></span>
      {:else if isDone}
        <Icon name="check-circle" size={14} />
      {/if}
      {method.label}
    </button>
  {/each}

  {#if runState === 'running'}
    <span class="text-muted" data-testid="arrhythmia-running">
      <span class="spinner" aria-hidden="true"></span>
      Running arrhythmia detection…
    </span>
  {/if}

  {#if error}
    <p class="text-danger" data-testid="arrhythmia-error">Arrhythmia detection failed: {error}</p>
  {/if}
</div>

<style>
  .arrhythmia-controls {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .arrhythmia-label {
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-right: var(--space-1);
  }

  /* Done state: green border/text/tint plus the check icon (the icon is what
     still tells it apart in the black & white theme, where success is black). */
  .btn-done {
    border-color: var(--color-success);
    color: var(--color-success);
    background: color-mix(in srgb, var(--color-success) 12%, transparent);
  }

  .spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    margin-right: var(--space-1);
    border: 2px solid var(--color-border);
    border-top-color: var(--color-accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    vertical-align: middle;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
