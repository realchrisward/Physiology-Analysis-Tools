<script lang="ts">
  import { detectArrhythmias } from '../../lib/api/arrhythmia'

  let {
    path,
    channel,
    onComplete,
    disabled = false,
  }: {
    path: string
    channel: string
    onComplete: () => void
    // Set by ReviewWorkspace to `!hydrationChecked` — see that component's
    // comment on the `<ArrhythmiaControls>` usage. ANDed with this
    // component's own `runState`-based button-disabling below rather than
    // replacing it, so a run already in flight still keeps the buttons
    // disabled even after `disabled` itself later flips back to `false`.
    // Defaults to `false` so every other caller/test that doesn't pass it
    // keeps today's behavior unchanged.
    disabled?: boolean
  } = $props()

  // Single state value for the whole re-run lifecycle, set synchronously
  // before the first `await` in `run` below — the same "one clear state
  // value, not multiple booleans that can drift out of sync" discipline
  // ImportScreen's `queueState` established (and ReviewWorkspace's own
  // `detectionRequestId`/`persistRequestId` guards reuse), rather than a
  // separate `running`/`disabled` pair.
  //   'idle'    - no re-run in flight; all three method buttons enabled.
  //   'running' - a re-run is in flight; all three method buttons disabled
  //               and a progress indicator is shown.
  type RunState = 'idle' | 'running'
  let runState: RunState = $state('idle')
  let error: string | null = $state(null)

  async function run(method: 'heuristic' | 'unsupervised' | 'both') {
    // Captured up front: the channel this specific run is FOR. `channel` is
    // a live prop (this component is mounted as `channel={activeChannel}`
    // in ReviewWorkspace, not remounted/reset via `{#key activeChannel}`),
    // so if the technician switches channels while `detectArrhythmias` below
    // is still in flight, `channel` will have already moved on by the time
    // it resolves — re-reading it post-`await` gives the CURRENT channel,
    // not the one this run was dispatched for.
    const requestedChannel = channel
    runState = 'running'
    error = null
    try {
      const result = await detectArrhythmias(path, requestedChannel, method)
      if (requestedChannel !== channel) return // superseded by a channel switch while this run was in flight

      if (result.status === 'ok' && !result.error) {
        // Only a genuinely successful run tells ReviewWorkspace to refresh
        // EcgGraph's beat markers — a failed/unchanged result must not
        // trigger a refetch against data that hasn't actually changed.
        onComplete()
      } else {
        error = result.error ?? 'Arrhythmia detection failed'
      }
    } finally {
      runState = 'idle'
    }
  }
</script>

<div data-testid="arrhythmia-controls">
  <button
    data-testid="run-heuristic-button"
    disabled={runState === 'running' || disabled}
    onclick={() => run('heuristic')}
  >
    Run heuristic
  </button>
  <button
    data-testid="run-unsupervised-button"
    disabled={runState === 'running' || disabled}
    onclick={() => run('unsupervised')}
  >
    Run unsupervised
  </button>
  <button
    data-testid="run-both-button"
    disabled={runState === 'running' || disabled}
    onclick={() => run('both')}
  >
    Run both
  </button>

  {#if runState === 'running'}
    <span data-testid="arrhythmia-running">Running arrhythmia detection…</span>
  {/if}

  {#if error}
    <p data-testid="arrhythmia-error">Arrhythmia detection failed: {error}</p>
  {/if}
</div>
