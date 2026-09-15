<script lang="ts">
  import { detectBeats } from '../../lib/api/beats'
  import { importFiles } from '../../lib/api/files'
  import { estimate, recordSample } from '../../lib/stores/eta'

  interface FileRow {
    path: string
    filename: string
    status: 'ready' | 'error' | 'detecting' | 'detected' | 'detection-error'
    channels: string[]
    defaultChannel: string | null
    size: number | null
    error: string | null
    beatCount: number | null
    meanHr: number | null
  }

  interface ReviewSelection {
    path: string
    channels: string[]
    defaultChannel: string
  }

  let { onReview }: { onReview?: (selection: ReviewSelection) => void } = $props()

  let rows: FileRow[] = $state([])
  let importError: string = $state('')
  let autoRun: boolean = $state(true)

  // Single source of truth for the whole import+detect lifecycle, covering
  // the picker dialog window, the import network round trip, and the
  // detection queue as one continuous guarded phase — so it is structurally
  // impossible for two import attempts to run concurrently (see
  // handleImportFiles/handleImportFolder, which set 'busy' synchronously
  // before awaiting anything).
  //   'idle'     - nothing in flight; Import Files/Import Folder enabled.
  //   'busy'     - picker open, import in flight, or detection queue
  //                running; Import Files/Import Folder disabled.
  //   'stopping' - Stop was clicked mid-queue; the in-flight file finishes,
  //                remaining files are skipped, then this resolves to
  //                'idle' via the same finally as the 'busy' path.
  type QueueState = 'idle' | 'busy' | 'stopping'
  let queueState: QueueState = $state('idle')

  async function importPaths(paths: string[]) {
    if (paths.length === 0) return

    importError = ''
    const result = await importFiles(paths)

    if ('error' in result) {
      importError = result.error
      return
    }

    const newRows: FileRow[] = []
    for (const fileResult of result.results) {
      rows.push({
        path: fileResult.path,
        filename: fileResult.filename,
        status: fileResult.status === 'ok' ? 'ready' : 'error',
        channels: fileResult.channels,
        defaultChannel: fileResult.default_channel,
        size: fileResult.size,
        error: fileResult.error,
        beatCount: null,
        meanHr: null,
      })
      // Capture the reference back out of the reactive `rows` array (rather
      // than holding onto the plain object literal above) so mutations made
      // later in the detection queue are tracked by Svelte's state proxy.
      newRows.push(rows[rows.length - 1])
    }

    if (autoRun) {
      await runDetectionQueue(newRows)
    }
  }

  async function runDetectionQueue(queue: FileRow[]) {
    for (const row of queue) {
      if (queueState === 'stopping') break
      if (row.status !== 'ready' || row.defaultChannel === null) continue

      row.status = 'detecting'
      const result = await detectBeats(row.path, row.defaultChannel)

      if (result.status === 'ok' && !result.error) {
        if (row.size !== null) {
          recordSample(row.size, result.elapsed_seconds)
        }
        row.status = 'detected'
        row.beatCount = result.count
        row.meanHr = result.mean_hr
      } else {
        row.status = 'detection-error'
        row.error = result.error
      }
    }
  }

  function handleReview(row: FileRow) {
    if (row.defaultChannel === null) return
    onReview?.({ path: row.path, channels: row.channels, defaultChannel: row.defaultChannel })
  }

  function handleStop() {
    if (queueState === 'busy') {
      queueState = 'stopping'
    }
  }

  function formatEta(size: number | null): string {
    if (size === null) return 'Calculating…'
    const seconds = estimate(size)
    if (seconds === null) return 'Calculating…'
    return `~${Math.max(1, Math.round(seconds))}s`
  }

  async function handleImportFiles() {
    // Set synchronously, before awaiting the picker, so the buttons are
    // disabled from the very first click — covering the picker-open
    // window, not just the later import/detection windows.
    queueState = 'busy'
    try {
      const paths = (await window.api?.pickFiles()) ?? []
      await importPaths(paths)
    } finally {
      queueState = 'idle'
    }
  }

  async function handleImportFolder() {
    queueState = 'busy'
    try {
      const paths = (await window.api?.pickFolder()) ?? []
      await importPaths(paths)
    } finally {
      queueState = 'idle'
    }
  }
</script>

<div>
  <div>
    <button data-testid="import-files-button" onclick={handleImportFiles} disabled={queueState !== 'idle'}>
      Import Files
    </button>
    <button data-testid="import-folder-button" onclick={handleImportFolder} disabled={queueState !== 'idle'}>
      Import Folder
    </button>
    <label>
      <input
        type="checkbox"
        data-testid="auto-run-checkbox"
        bind:checked={autoRun}
        disabled={queueState !== 'idle'}
      />
      Auto-run beat detection
    </label>
    <button data-testid="stop-button" onclick={handleStop}>Stop</button>
  </div>

  {#if importError}
    <p data-testid="import-error">Import failed: {importError}</p>
  {/if}

  <ul>
    {#each rows as row (row.path)}
      <li data-testid="file-row">
        <span>{row.filename}</span>
        {#if row.status === 'ready'}
          <span>ready</span>
        {:else if row.status === 'detecting'}
          <span>detecting…</span>
          <span data-testid="eta-badge">{formatEta(row.size)}</span>
        {:else if row.status === 'detected'}
          <span>{row.beatCount} beats detected{row.meanHr !== null ? `, mean HR ${row.meanHr}` : ''}</span>
        {:else if row.status === 'detection-error'}
          <span>detection failed: {row.error}</span>
        {:else}
          <span>{row.error}</span>
        {/if}
        {#if row.defaultChannel !== null && row.status !== 'error'}
          <button data-testid="review-button" onclick={() => handleReview(row)}>Review</button>
        {/if}
      </li>
    {/each}
  </ul>
</div>
