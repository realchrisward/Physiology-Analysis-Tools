<script lang="ts">
  import { importFiles } from '../../lib/api/files'

  interface FileRow {
    path: string
    filename: string
    status: 'ready' | 'error'
    channels: string[]
    defaultChannel: string | null
    size: number | null
    error: string | null
  }

  let rows: FileRow[] = $state([])
  let importError: string = $state('')

  async function importPaths(paths: string[]) {
    if (paths.length === 0) return

    importError = ''
    const result = await importFiles(paths)

    if ('error' in result) {
      importError = result.error
      return
    }

    for (const fileResult of result.results) {
      rows.push({
        path: fileResult.path,
        filename: fileResult.filename,
        status: fileResult.status === 'ok' ? 'ready' : 'error',
        channels: fileResult.channels,
        defaultChannel: fileResult.default_channel,
        size: fileResult.size,
        error: fileResult.error,
      })
    }
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
    <button data-testid="import-files-button" onclick={handleImportFiles}>Import Files</button>
    <button data-testid="import-folder-button" onclick={handleImportFolder}>Import Folder</button>
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
        {:else}
          <span>{row.error}</span>
        {/if}
      </li>
    {/each}
  </ul>
</div>
