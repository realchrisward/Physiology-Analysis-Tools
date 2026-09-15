<script lang="ts">
  import { detectBeats } from '../../lib/api/beats'

  interface DetectionSummary {
    status: 'ok' | 'error' | null
    count: number | null
    meanHr: number | null
    duration: number | null
    error: string | null
  }

  let {
    path,
    channels,
    defaultChannel,
  }: {
    path: string
    channels: string[]
    defaultChannel: string
  } = $props()

  // Seeded from the prop once; intentionally does not re-sync if a parent
  // ever re-renders this component with a different `defaultChannel` for
  // the same file (Svelte's compiler flags this pattern as
  // state_referenced_locally — expected here since the user's own channel
  // selection must always win from this point on).
  let selectedChannel: string = $state(defaultChannel)
  let detection: DetectionSummary = $state({
    status: null,
    count: null,
    meanHr: null,
    duration: null,
    error: null,
  })

  async function handleChannelChange() {
    const result = await detectBeats(path, selectedChannel)

    if (result.status === 'ok' && !result.error) {
      detection = {
        status: 'ok',
        count: result.count,
        meanHr: result.mean_hr,
        duration: result.duration,
        error: null,
      }
    } else {
      detection = {
        status: 'error',
        count: null,
        meanHr: null,
        duration: null,
        error: result.error,
      }
    }
  }
</script>

<div data-testid="review-workspace">
  <select data-testid="channel-select" bind:value={selectedChannel} onchange={handleChannelChange}>
    {#each channels as channel}
      <option value={channel}>{channel}</option>
    {/each}
  </select>

  {#if detection.status === 'ok'}
    <div data-testid="detection-summary">
      {detection.count} beats detected{detection.meanHr !== null ? `, mean HR ${detection.meanHr}` : ''}{detection.duration !==
      null
        ? `, duration ${detection.duration}s`
        : ''}
    </div>
  {:else if detection.status === 'error'}
    <div data-testid="detection-summary">detection failed: {detection.error}</div>
  {/if}

  <!-- Task 2/3 mount EcgGraph here, in this same file, bound to `path` /
       `selectedChannel`. -->
  <div class="graph-area"></div>
</div>
