<script lang="ts">
  import ImportScreen from './components/import/ImportScreen.svelte'
  import ReviewWorkspace from './components/review/ReviewWorkspace.svelte'

  interface ReviewSelection {
    path: string
    channels: string[]
    defaultChannel: string
  }

  let view: 'import' | 'review' = $state('import')
  let selectedFile: ReviewSelection | null = $state(null)

  function handleReview(row: ReviewSelection) {
    selectedFile = row
    view = 'review'
  }

  function handleBack() {
    view = 'import'
  }
</script>

<main>
  <h1>Physiology Analysis Tools</h1>
  {#if view === 'import'}
    <ImportScreen onReview={handleReview} />
  {:else if view === 'review' && selectedFile}
    <button data-testid="back-to-import-button" onclick={handleBack}>Back to Import</button>
    <ReviewWorkspace
      path={selectedFile.path}
      channels={selectedFile.channels}
      defaultChannel={selectedFile.defaultChannel}
    />
  {/if}
</main>
