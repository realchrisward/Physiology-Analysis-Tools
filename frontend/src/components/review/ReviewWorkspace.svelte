<script lang="ts">
  import { onMount } from 'svelte'
  import { detectBeats } from '../../lib/api/beats'
  import { generateReport, getFileState, persistBeats, putChannel } from '../../lib/api/persistence'
  import EcgGraph from '../graph/EcgGraph.svelte'
  import ArrhythmiaControls from './ArrhythmiaControls.svelte'
  import BeatCategoryPanel from './BeatCategoryPanel.svelte'
  import type { BadDataMark, CategoryUpdateResult, WindowBeat } from '../../lib/api/types'

  interface DetectionSummary {
    status: 'pending' | 'ok' | 'error' | null
    count: number | null
    meanHr: number | null
    duration: number | null
    error: string | null
  }

  // F5's report export lifecycle — same single-state-value discipline as
  // `DetectionSummary` above (and `ArrhythmiaControls`' own `RunState`):
  // one status field, not a separate pending/success/error boolean trio that
  // could drift out of sync.
  //   'idle'       - no export in flight, nothing to show yet.
  //   'generating' - `POST /files/report` is in flight.
  //   'ok'         - succeeded; `outputPath` holds the real path the backend
  //                  actually wrote to (never a fabricated/guessed one).
  //   'error'      - failed; `error` holds the backend's message verbatim.
  interface ReportState {
    status: 'idle' | 'generating' | 'ok' | 'error'
    outputPath: string | null
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

  // F5 reopen hydration: the prior session's bad-data marks, if any, fetched
  // via `GET /files/state` in the `onMount` below and passed straight
  // through as EcgGraph's `initialBadDataMarks` prop (see that prop's own
  // comment in EcgGraph.svelte). Stays `[]` — EcgGraph's own default — for a
  // brand-new file (`found === false`) or if the state check itself fails.
  let initialBadDataMarks: BadDataMark[] = $state([])

  // Set `true` once the mount-time `getFileState` call below has settled —
  // found a record, found none, or errored, it doesn't matter which; see
  // that `onMount`'s own `finally`. Gates two things in the template below:
  // the channel `<select>` (`disabled={!hydrationChecked}`) and whether
  // `<EcgGraph>` renders at all. Without this, two real bugs follow from
  // `EcgGraph` mounting (and the `<select>` being interactive) synchronously
  // at t=0, before hydration's network round trip can possibly have
  // resolved: (1) `EcgGraph`'s `initialBadDataMarks` is a one-time seed
  // (see that prop's own comment in EcgGraph.svelte) — if the persisted
  // channel happens to equal `defaultChannel` (the common case: reopening a
  // file on the same channel you left it on), `{#key activeChannel}` never
  // sees the key value actually change, so no remount ever occurs to pick
  // up the hydrated marks, and they're silently dropped. (2) Whether or not
  // the channels match, `EcgGraph` fires a real, wasted `/channels/window`
  // (and chained `/beats/window`) fetch against `defaultChannel` on every
  // reopen where the persisted channel differs from it, with a real chance
  // of a visibly-wrong chart briefly painting before the remount catches up.
  // Holding both the graph and the `<select>` back until hydration has
  // already set `selectedChannel`/`activeChannel`/`initialBadDataMarks` to
  // their final values sidesteps both problems by construction — `EcgGraph`
  // only ever mounts once, already correctly configured — and additionally
  // makes `handleChannelChange` structurally unable to fire while a hydration
  // write is still in flight, closing a third race: without this, a
  // technician changing the channel mid-hydration and the hydration write
  // itself could both write `selectedChannel`/`activeChannel`/
  // `lastPersistedChannel` with no awareness of each other, and whichever
  // resolved last would silently win. `getFileState` is a single, normally
  // fast round trip, so this is a brief gap, not a perceptible delay — the
  // `<select>`/detection-summary UI above still renders immediately either
  // way, only the graph area waits.
  let hydrationChecked: boolean = $state(false)

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

  // Bumped on every successful arrhythmia re-run (see `ArrhythmiaControls`'s
  // `onComplete` below) and passed straight through as EcgGraph's own
  // `beatsRefreshToken` prop. EcgGraph watches it via an `$effect` (mirroring
  // the same "record the previous value, skip the first/no-op run" shape as
  // its own `containerWidth` effect) and, on any actual change, re-fetches
  // ONLY `/beats/window` for its current visible range and re-merges it
  // against the waveform data already on screen — no channel-data re-fetch,
  // no viewport reset. A plain incrementing counter (rather than a boolean
  // or a one-shot callback prop) is used so EcgGraph's effect can tell "the
  // parent is asking again" apart from "nothing changed" without needing any
  // acknowledgement back from EcgGraph.
  let beatsRefreshToken = $state(0)

  function refreshGraphBeats() {
    beatsRefreshToken += 1
  }

  // F5's report export. See `ReportState`'s own declaration above for the
  // state shape.
  let report: ReportState = $state({ status: 'idle', outputPath: null, error: null })

  // Opens Electron's native directory picker (F1's IPC bridge,
  // `window.api.pickOutputDirectory`) and, unless the technician cancels it
  // (a `null` resolution — a deliberate no-op, not an error condition), asks
  // the backend to write the report there via `POST /files/report`
  // (`generateReport`). `window.api` itself is optional (see
  // `electron.d.ts`) for the same reason `HealthStatus`/`ImportScreen`
  // already guard every `window.api` call — the web build has no Electron
  // bridge at all — so a missing bridge is treated identically to a
  // cancellation rather than thrown.
  async function handleGenerateReport() {
    const dir = await window.api?.pickOutputDirectory()
    if (!dir) return // canceled, or no Electron bridge present

    report = { status: 'generating', outputPath: null, error: null }
    const result = await generateReport(path, dir)
    if (result.status === 'ok' && !result.error && result.output_path) {
      report = { status: 'ok', outputPath: result.output_path, error: null }
    } else {
      report = { status: 'error', outputPath: null, error: result.error ?? 'Failed to generate report' }
    }
  }

  // F5 reopen hydration: checks whether this file already has a persisted
  // review in progress, once, when the workspace first mounts (see
  // `HealthStatus.svelte` for this codebase's existing
  // `onMount(async () => {...})` idiom for one-time-on-mount async work).
  //
  // A found record means Task 1's backend restore (`df02d2c`) has already
  // rebuilt `beat_cache[path]` for `channel` — so this deliberately skips
  // the `detectBeats`-then-advance dance `handleChannelChange` uses for a
  // fresh channel switch (see its own comment) and sets `selectedChannel`/
  // `activeChannel` directly: re-running detection here would be redundant
  // work against data the backend already has ready.
  //
  // `lastPersistedChannel` is also seeded to the persisted channel — it
  // already reflects the backend's persisted `beats` rows for this
  // (path, channel) pair (that's exactly what made it "found"), so the
  // first beat selection on this channel this session shouldn't re-fire a
  // wasted `POST /files/beats` via `ensureChannelPersisted`.
  //
  // `found === false` (a brand-new file) or any failure of the state check
  // itself (network error, non-2xx — `getFileState`/`apiGet` never throws,
  // converting either into `{status: 'error', ...}`, whose `found` is
  // simply absent/falsy here) both fall through unchanged: `selectedChannel`/
  // `activeChannel` stay at `defaultChannel`, `initialBadDataMarks` stays
  // `[]`, and `lastPersistedChannel` stays `null` — exactly today's
  // pre-F5 behavior. A state-check failure must never be worse than
  // starting fresh.
  onMount(async () => {
    try {
      const result = await getFileState(path)
      if (result.found && result.channel) {
        selectedChannel = result.channel
        activeChannel = result.channel
        initialBadDataMarks = result.bad_data_marks ?? []
        lastPersistedChannel = result.channel
      }
    } finally {
      // Every exit path — found, not-found, or an error/rejection getFileState
      // itself never actually produces (see its own "never throws" comment)
      // but this stays defensive about regardless — lands here. See
      // `hydrationChecked`'s own declaration above for what this unblocks.
      hydrationChecked = true
    }
  })

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

  // Shared by both `ensureChannelPersisted` (below — skips the call
  // entirely if `channel` was already the last-persisted one) and
  // `handleArrhythmiaComplete` (below — deliberately calls this
  // unconditionally, bypassing that skip, since a successful arrhythmia
  // rerun changes `beat_cache[path]` even for a channel that was already
  // persisted earlier this session). Both call sites share the SAME
  // `persistRequestId` counter, so a stale response from either one can't
  // clobber a newer persist's result regardless of which call site issued
  // it — mirroring the pre-existing out-of-order-response guard this
  // function's body already implemented before being pulled out here.
  async function persistChannelData(channel: string) {
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

  async function ensureChannelPersisted(channel: string) {
    if (lastPersistedChannel === channel) return
    await persistChannelData(channel)
  }

  function handleBeatSelect(beat: WindowBeat) {
    selectedBeat = beat
    if (lastPersistedChannel !== activeChannel) {
      void ensureChannelPersisted(activeChannel)
    }
  }

  function handleCategoryUpdated(result: CategoryUpdateResult) {
    // BeatCategoryPanel already reflects the new review_state/
    // reassigned_category in its own local state (see that component) —
    // nothing further to do here for that. But a successful confirm/reject/
    // reassign also means the backend's `beat_cache[path]` is now in sync
    // (Task 1, `df02d2c`'s reopen/mutation sync) — so refresh EcgGraph's
    // beat markers via the same `beatsRefreshToken` mechanism
    // ArrhythmiaControls' `onComplete` already uses (reusing that one path
    // rather than building a second beats-refresh mechanism). This closes
    // F4's Important finding #2: previously the graph's marker for this
    // beat stayed stuck at its pre-review state until a full channel
    // switch remounted EcgGraph. A failed/unchanged result must not
    // trigger a refetch against data that hasn't actually changed.
    //
    // Deliberately does NOT also call `persistChannelData` (unlike
    // `handleArrhythmiaComplete` below): `PATCH /files/beats/category`
    // already writes this one beat's review state directly to SQLite
    // (`db.update_beat_category`), so a wholesale `POST /files/beats`
    // re-persist here would be redundant — Part 1's replace_beats fix
    // makes it *safe* to call again, not *useful* to call again on this
    // path.
    if (result.status === 'ok' && !result.error) {
      refreshGraphBeats()
    }
  }

  // Passed as `ArrhythmiaControls`' `onComplete` prop — DISTINCT from
  // `handleCategoryUpdated` above. A successful arrhythmia rerun (`POST
  // /arrhythmia/detect`) only updates the in-memory `beat_cache[path]` on
  // the backend; unlike a category action, nothing else writes that result
  // to SQLite. Without this, a rerun after some beats were already
  // reviewed would leave the *displayed* graph correct (via
  // `refreshGraphBeats` below) while the *persisted* (and therefore
  // reported/exported) data silently went stale — reproducible via: select
  // a beat (persists channel) → confirm/reject it → rerun arrhythmia →
  // generate report → the .xlsx reflects the pre-rerun detection.
  //
  // Calls `persistChannelData` directly (not `ensureChannelPersisted`):
  // this channel may already be `lastPersistedChannel` from an earlier beat
  // selection, but the underlying `beat_cache[path]` just changed, so the
  // persist must happen again regardless — `ensureChannelPersisted`'s
  // skip-if-already-persisted guard exists for a different reason (avoiding
  // a redundant persist on a second beat *selection* for the same,
  // unchanged, channel) and would wrongly suppress this one. This is safe
  // to call repeatedly now (Part 1's `replace_beats` fix in `backend/db.py`
  // preserves any beat's prior `review_state`/`reassigned_category` across
  // a re-persist), so it does not undo any confirm/reject/reassign already
  // applied on this channel.
  async function handleArrhythmiaComplete() {
    refreshGraphBeats()
    await persistChannelData(activeChannel)
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
      // Remember this channel choice for next time this file is reopened
      // (F5's `GET /files/state` hydration above reads it back). Deliberately
      // fire-and-forget: `putChannel`/`apiPost` never throws (see http.ts),
      // and even if it failed, that must never roll back the channel switch
      // that has already succeeded above — it's a nice-to-have next to
      // detection actually working, not a blocking precondition.
      void putChannel(path, channel)
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
  <select
    data-testid="channel-select"
    bind:value={selectedChannel}
    onchange={handleChannelChange}
    disabled={!hydrationChecked}
  >
    {#each channels as channel}
      <option value={channel}>{channel}</option>
    {/each}
  </select>

  <!-- `disabled={!hydrationChecked}` — same reasoning as the `<select>`'s
       own `disabled` above: `activeChannel` still holds `defaultChannel`
       until the mount-time `getFileState` hydration resolves, so a rerun
       triggered before that settles would run arrhythmia detection against
       the wrong (pre-hydration) channel on reopen. Uses a `disabled` prop
       ANDed with ArrhythmiaControls' own `runState`-based button-disabling
       (see that component) rather than wrapping this in `{#if
       hydrationChecked}` — that would work too, but a prop keeps this
       component mounted (and its `channel` prop live-updating) across the
       hydration boundary exactly like the `<select>` above, instead of an
       unmount/remount that briefly hides these controls entirely. -->
  <ArrhythmiaControls
    path={path}
    channel={activeChannel}
    onComplete={handleArrhythmiaComplete}
    disabled={!hydrationChecked}
  />

  <button
    data-testid="generate-report-button"
    disabled={report.status === 'generating'}
    onclick={handleGenerateReport}
  >
    Generate Report
  </button>

  {#if report.status === 'generating'}
    <span data-testid="report-generating">Generating report…</span>
  {:else if report.status === 'ok'}
    <p data-testid="report-success">Report saved to {report.outputPath}</p>
  {:else if report.status === 'error'}
    <p data-testid="report-error">Report generation failed: {report.error}</p>
  {/if}

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
    <!-- Gated on `hydrationChecked` — see its own declaration above. Nothing
         renders here at all until the mount-time `getFileState` hydration
         has settled, so the one and only mount of EcgGraph below is always
         already configured with the final, correct `activeChannel`/
         `initialBadDataMarks` — no transient fetch against `defaultChannel`,
         and no reliance on a later remount to pick up hydrated marks.

         Keyed on `activeChannel`, NOT `selectedChannel` — see the
         `activeChannel` declaration above for why the two are split.
         EcgGraph remounts (rather than updating its `channel` prop in
         place) whenever the active channel changes after this — EcgGraph's
         own fetch/uPlot-construction logic runs once, in onMount, so a
         remount is the simplest way to get it to re-init for a new
         channel's data. -->
    {#if hydrationChecked}
      {#key activeChannel}
        <EcgGraph
          {path}
          channel={activeChannel}
          onBeatSelect={handleBeatSelect}
          {beatsRefreshToken}
          {initialBadDataMarks}
        />
      {/key}
    {/if}
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
