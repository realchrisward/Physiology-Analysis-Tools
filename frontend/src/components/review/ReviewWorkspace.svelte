<script lang="ts">
  import { detectBeats } from '../../lib/api/beats'
  import { persistBeats } from '../../lib/api/persistence'
  import EcgGraph from '../graph/EcgGraph.svelte'
  import BeatCategoryPanel from './BeatCategoryPanel.svelte'
  import type { CategoryUpdateResult, WindowBeat } from '../../lib/api/types'

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

  // The beat most recently selected via a click on EcgGraph's marker (see
  // `onBeatSelect` below), driving the real `BeatCategoryPanel` below.
  let selectedBeat: WindowBeat | null = $state(null)

  // `POST /files/beats` (F5's persistence step, pulled forward here) has to
  // succeed for a given (path, channel) pair before the backend's
  // `PATCH /files/beats/category` will accept any confirm/reject/reassign
  // for it — see BACKEND_OVERVIEW.md and `backend/db_routes.py`'s
  // `update_beat_category`, which 404s with "No persisted data for this
  // file" until a `files` row exists.
  //
  // Trigger point chosen: EAGERLY, the first time a beat is selected for a
  // given channel this session — not lazily on the first confirm/reject/
  // reassign click inside `BeatCategoryPanel`. `BeatCategoryPanel`'s own
  // contract (path/beat/onUpdated) has no hook for a parent to intercept or
  // delay its button clicks, so the only place this component can enforce
  // "never let a category action reach the API before persistBeats has
  // succeeded" is by controlling *whether `BeatCategoryPanel` is mounted at
  // all* — gating on `selectedBeat` becoming non-null (which is also
  // `BeatCategoryPanel`'s own mount condition) achieves that structurally,
  // with no need for `BeatCategoryPanel` to know this gate exists.
  //
  // Tracked as a SINGLE last-persisted channel, not a per-channel set:
  // `POST /files/beats` → the backend's `db.replace_beats()` does a
  // `DELETE FROM beats WHERE file_id=?` (scoped only to the file — there is
  // no `channel` column on the `beats` table at all) and then reinserts, for
  // ONE channel at a time. So persisting channel B after channel A wipes
  // out channel A's persisted rows; the backend can only ever have ONE
  // channel's beats persisted at once. `null` means nothing has been
  // persisted yet this session.
  let lastPersistedChannel: string | null = $state(null)
  let persistPending: boolean = $state(false)
  let persistError: string | null = $state(null)

  // Guards against out-of-order responses if a newer `persistBeats` call is
  // dispatched (e.g. the technician switches channels and selects a beat
  // there) while a previous one is still in flight — same pattern as
  // `detectionRequestId` above, and for the same underlying reason: two
  // requests dispatched in order can still complete out of order over a real
  // network. Without this, a slow channel-1 response arriving AFTER a
  // faster channel-2 response would clobber `lastPersistedChannel` back to
  // `'channel 1'`, even though the backend (which can only hold one
  // channel's persisted beats at a time — see the comment above) actually
  // has channel 2's data. Only the response matching the most recently
  // issued `persistBeats` call is allowed to update `lastPersistedChannel`/
  // `persistPending`/`persistError`.
  let persistRequestId = 0

  // Whenever `activeChannel` actually advances (see its declaration above),
  // any selection/persist state from the previous channel is stale and must
  // be cleared — `selected-beat-panel` is a SIBLING of the `{#key
  // activeChannel}`-keyed <EcgGraph>, not nested inside it, so switching
  // channels does not otherwise remount/reset it. Without this, switching
  // channels via the <select> (with no new beat click yet) would leave
  // `BeatCategoryPanel` mounted showing the previous channel's beat as if it
  // belonged to the new one. `lastPersistedChannel` is deliberately NOT
  // reset here — it tracks backend truth (which channel, if any, is
  // actually persisted), independent of what's currently selected in the UI.
  $effect(() => {
    activeChannel
    selectedBeat = null
    persistPending = false
    persistError = null
  })

  async function ensureChannelPersisted(channel: string) {
    if (lastPersistedChannel === channel) return

    const requestId = ++persistRequestId
    persistPending = true
    persistError = null
    const result = await persistBeats(path, channel)
    if (requestId !== persistRequestId) return // superseded by a newer persist call

    persistPending = false

    if (result.status === 'ok' && !result.error) {
      lastPersistedChannel = channel
    } else {
      persistError = result.error ?? 'Failed to prepare review data'
    }
  }

  function handleBeatSelect(beat: WindowBeat) {
    selectedBeat = beat
    if (lastPersistedChannel !== activeChannel) {
      void ensureChannelPersisted(activeChannel)
    }
  }

  function handleCategoryUpdated(_result: CategoryUpdateResult) {
    // BeatCategoryPanel already reflects the new review_state/
    // reassigned_category in its own local state (see that component) —
    // nothing further to do here yet. This hook exists so a later milestone
    // (e.g. a beat-list summary elsewhere on this screen) has a place to
    // react to updates without changing BeatCategoryPanel's contract.
  }

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
      <EcgGraph {path} channel={activeChannel} onBeatSelect={handleBeatSelect} />
    {/key}
  </div>

  <div data-testid="selected-beat-panel">
    {#if selectedBeat === null}
      <p data-testid="selected-beat-summary">No beat selected</p>
    {:else if persistPending}
      <p data-testid="persist-pending">Preparing review data for {activeChannel}…</p>
    {:else if persistError && lastPersistedChannel !== activeChannel}
      <div data-testid="persist-error">
        <p>Could not prepare review data: {persistError}</p>
        <button data-testid="persist-retry-button" onclick={() => ensureChannelPersisted(activeChannel)}>
          Retry
        </button>
      </div>
    {:else}
      {#key selectedBeat.ts}
        <BeatCategoryPanel {path} beat={selectedBeat} onUpdated={handleCategoryUpdated} />
      {/key}
    {/if}
  </div>
</div>
