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

  let rows: FileRow[] = $state([])
  let importError: string = $state('')
  let autoRun: boolean = $state(true)
  let stopped: boolean = $state(false)
  let queueRunning: boolean = $state(false)

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
    // Guarded by `queueRunning` disabling the import buttons (see markup),
    // so a second call can never start while this one is in flight — the
    // `stopped` reset below can no longer clobber an in-progress Stop.
    queueRunning = true
    stopped = false
    try {
      for (const row of queue) {
        if (stopped) break
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
    } finally {
      queueRunning = false
    }
  }

  function handleStop() {
    stopped = true
  }

  function formatEta(size: number | null): string {
    if (size === null) return 'Calculating…'
    const seconds = estimate(size)
    if (seconds === null) return 'Calculating…'
    return `~${Math.max(1, Math.round(seconds))}s`
  }

  async function handleImportFiles() {
    const paths = (await window.api?.pickFiles()) ?? []
    await importPaths(paths)
  }

  async function handleImportFolder() {
    const paths = (await window.api?.pickFolder()) ?? []
    await importPaths(paths)
  }
</script>

<div>
  <div>
    <button data-testid="import-files-button" onclick={handleImportFiles} disabled={queueRunning}>
      Import Files
    </button>
    <button data-testid="import-folder-button" onclick={handleImportFolder} disabled={queueRunning}>
      Import Folder
    </button>
    <label>
      <input type="checkbox" data-testid="auto-run-checkbox" bind:checked={autoRun} disabled={queueRunning} />
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
      </li>
    {/each}
  </ul>
</div>
