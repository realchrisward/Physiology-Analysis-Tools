<script lang="ts">
  import { detectBeats } from '../../lib/api/beats'
  import EcgGraph from '../graph/EcgGraph.svelte'

  interface DetectionSummary {
    status: 'pending' | 'ok' | 'error' | null
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

  // Captured once as a plain (non-reactive) value so seeding two separate
  // `$state`s from it below only trips Svelte's state_referenced_locally
  // compiler warning once instead of twice — both intentionally do not
  // re-sync if a parent ever re-renders this component with a different
  // `defaultChannel` for the same file (the user's own channel
  // selection/detection progress must always win from this point on).
  const initialChannel = defaultChannel

  // Seeded from the prop once; see `initialChannel` above.
  let selectedChannel: string = $state(initialChannel)
  // `selectedChannel` (above) is only what the <select> shows — it updates
  // the instant the technician picks a new option. `activeChannel` is what
  // actually props/keys <EcgGraph>, and only ever advances to a channel
  // once `detectBeats` has resolved successfully *for that channel*, so the
  // graph never remounts against a channel whose `beat_cache[path]` entry
  // (keyed only by path on the backend, not by channel — see
  // `backend/beats.py`/`backend/beats_window.py`) doesn't reflect it yet.
  // Without this split, EcgGraph's onMount fetch (a cheap dataframe filter)
  // routinely wins the race against the real detectBeats computation,
  // rendering the previous channel's stale beat markers — or none at all —
  // over the new channel's waveform. Starts equal to `defaultChannel`
  // because the Import screen's auto-run already populated the cache for
  // it before Review was ever opened, so no re-detection is needed for the
  // very first channel shown.
  let activeChannel: string = $state(initialChannel)
  let detection: DetectionSummary = $state({
    status: null,
    count: null,
    meanHr: null,
    duration: null,
    error: null,
  })

  // Guards against out-of-order responses if the technician switches
  // channels again while a previous `detectBeats` call is still in flight:
  // only the response matching the most recently issued request is allowed
  // to update `detection`/advance `activeChannel`.
  let detectionRequestId = 0

  async function handleChannelChange() {
    const channel = selectedChannel
    const requestId = ++detectionRequestId

    detection = { status: 'pending', count: null, meanHr: null, duration: null, error: null }

    const result = await detectBeats(path, channel)
    if (requestId !== detectionRequestId) return // superseded by a newer channel switch

    if (result.status === 'ok' && !result.error) {
      detection = {
        status: 'ok',
        count: result.count,
        meanHr: result.mean_hr,
        duration: result.duration,
        error: null,
      }
      // Only now — with `beat_cache[path]` on the backend guaranteed to
      // reflect this channel's detection — advance the channel EcgGraph is
      // keyed/mounted against.
      activeChannel = channel
    } else {
      detection = {
        status: 'error',
        count: null,
        meanHr: null,
        duration: null,
        error: result.error,
      }
      // `activeChannel` deliberately does not advance: the graph keeps
      // showing the last-known-good channel's data rather than switching to
      // one detection just failed for.
    }
  }
</script>

<div data-testid="review-workspace">
  <select data-testid="channel-select" bind:value={selectedChannel} onchange={handleChannelChange}>
    {#each channels as channel}
      <option value={channel}>{channel}</option>
    {/each}
  </select>

  {#if detection.status === 'pending'}
    <div data-testid="detection-summary">detecting beats for {selectedChannel}…</div>
  {:else if detection.status === 'ok'}
    <div data-testid="detection-summary">
      {detection.count} beats detected{detection.meanHr !== null ? `, mean HR ${detection.meanHr}` : ''}{detection.duration !==
      null
        ? `, duration ${detection.duration}s`
        : ''}
    </div>
  {:else if detection.status === 'error'}
    <div data-testid="detection-summary">detection failed: {detection.error}</div>
  {/if}

  <div class="graph-area">
    <!-- Keyed on `activeChannel`, NOT `selectedChannel` — see the
         `activeChannel` declaration above for why the two are split.
         EcgGraph remounts (rather than updating its `channel` prop in
         place) whenever the active channel changes — EcgGraph's own
         fetch/uPlot-construction logic runs once, in onMount, so a remount
         is the simplest way to get it to re-init for a new channel's
         data. -->
    {#key activeChannel}
      <EcgGraph {path} channel={activeChannel} />
    {/key}
  </div>
</div>
