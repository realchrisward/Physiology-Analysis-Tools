<script lang="ts">
  import { detectArrhythmias } from '../../lib/api/arrhythmia'

  let {
    path,
    channel,
    onComplete,
  }: {
    path: string
    channel: string
    onComplete: () => void
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
    runState = 'running'
    error = null
    try {
      const result = await detectArrhythmias(path, channel, method)
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
  <button data-testid="run-heuristic-button" disabled={runState === 'running'} onclick={() => run('heuristic')}>
    Run heuristic
  </button>
  <button
    data-testid="run-unsupervised-button"
    disabled={runState === 'running'}
    onclick={() => run('unsupervised')}
  >
    Run unsupervised
  </button>
  <button data-testid="run-both-button" disabled={runState === 'running'} onclick={() => run('both')}>
    Run both
  </button>

  {#if runState === 'running'}
    <span data-testid="arrhythmia-running">Running arrhythmia detection…</span>
  {/if}

  {#if error}
    <p data-testid="arrhythmia-error">Arrhythmia detection failed: {error}</p>
  {/if}
</div>
