<script lang="ts">
  import { onMount } from 'svelte'
  import { detectBeats } from '../../lib/api/beats'
  import { detectArrhythmias } from '../../lib/api/arrhythmia'
  import { getBeatsOfInterest } from '../../lib/api/windowing'
  import {
    discardFileState,
    generateReport,
    getFileState,
    persistBeats,
    putChannel,
  } from '../../lib/api/persistence'
  import EcgGraph from '../graph/EcgGraph.svelte'
  import ArrhythmiaControls from './ArrhythmiaControls.svelte'
  import BeatCategoryPanel from './BeatCategoryPanel.svelte'
  import Icon from '../shared/Icon.svelte'
  import { fileRegistry } from '../../lib/stores/fileRegistry.svelte'
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
  // True while the graph is in focus mode and shows the beat controls in its
  // own popover; the side panel then renders none, so there is only ever one
  // BeatCategoryPanel (and one set of C/R key listeners) mounted.
  let focusMode: boolean = $state(false)

  // Collapsible beat-detail side panel — collapsed, it shrinks to a slim
  // icon rail so the graph gets the full width; a technician can collapse
  // it manually while just panning/browsing the waveform. Auto-expands
  // back the moment a NEW beat is selected (see the `$effect` below) so
  // selecting a beat always shows its detail without an extra click, but a
  // technician who collapses it again while looking at the SAME beat has
  // that choice respected until they pick a different one.
  let beatPanelCollapsed: boolean = $state(false)
  let previousSelectedBeatTs: number | null = null

  function toggleBeatPanel(): void {
    beatPanelCollapsed = !beatPanelCollapsed
  }

  $effect(() => {
    const ts = selectedBeat?.ts ?? null
    if (ts !== null && ts !== previousSelectedBeatTs) {
      beatPanelCollapsed = false
    }
    previousSelectedBeatTs = ts
  })

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

  // Every flagged beat's timestamp for the whole file — what EcgGraph's
  // beat-of-interest navigation steps through, and the count its legend
  // shows. Refreshed from the backend (rather than derived from the graph's
  // own viewport-scoped beat fetch) after anything that can change which
  // beats are flagged: detection running, an arrhythmia re-run, or a
  // confirm/reject/reassign.
  let beatsOfInterest: number[] = $state([])
  // Typical RR interval (seconds) from the same response; sizes the graph's
  // focus view around a beat of interest.
  let typicalRr: number | null = $state(null)
  // Guards the automatic on-load arrhythmia run so it happens once per
  // channel, not on every reactive re-run.
  let autoArrhythmiaChannel: string | null = null
  let autoArrhythmiaRunning: boolean = $state(false)
  // The automatic run is a multi-step chain (check → detect → persist →
  // refresh) that can outlive the component if the technician navigates away
  // mid-run. Checked at every await boundary so an unmounted workspace stops
  // issuing requests and stops writing to state instead of running to
  // completion against a screen nobody is looking at.
  let destroyed = false
  onMount(() => () => {
    destroyed = true
  })

  // Resolves to whether the backend actually has beats for this file. An
  // error means beat detection hasn't run yet, which is NOT the same as
  // "has beats, none flagged" — only the latter is worth auto-running
  // arrhythmia detection for.
  async function refreshBeatsOfInterest(): Promise<'has-beats' | 'no-beats'> {
    const result = await getBeatsOfInterest(path)
    if (destroyed) return 'no-beats'
    if (result.status !== 'ok' || result.error) {
      beatsOfInterest = []
      typicalRr = null
      return 'no-beats'
    }
    beatsOfInterest = result.ts ?? []
    typicalRr = result.typical_rr ?? null
    return 'has-beats'
  }

  // Runs arrhythmia detection automatically once a channel's beats are
  // ready, so the graph can open focused on the first beat of interest
  // instead of on the whole recording. The technician can still re-run any
  // method explicitly from ArrhythmiaControls afterwards.
  //
  // Tries "both" first (heuristic rules plus the shape-based clustering, the
  // fullest picture), and falls back to heuristic-only if that fails: the
  // unsupervised method needs enough usable beat epochs to cluster, and a
  // short or sparse recording legitimately can't provide them. On the manual
  // buttons that failure is worth surfacing verbatim; on an automatic run it
  // should quietly degrade to the detection that CAN run rather than leaving
  // the technician with nothing.
  async function autoRunArrhythmiaDetection(channel: string) {
    autoArrhythmiaRunning = true
    try {
      let result = await detectArrhythmias(path, channel, 'both')
      if (destroyed) return
      if (result.status !== 'ok' || result.error) {
        result = await detectArrhythmias(path, channel, 'heuristic')
        if (destroyed) return
      }
      // A channel switch while this was in flight makes the result stale —
      // same channel-identity guard every other async operation here uses.
      if (channel !== activeChannel) return
      if (result.status === 'ok' && !result.error) {
        await persistChannelData(channel)
        if (destroyed) return
        refreshGraphBeats()
        await refreshBeatsOfInterest()
      }
    } finally {
      if (!destroyed && channel === activeChannel) autoArrhythmiaRunning = false
    }
  }

  // Drives the automatic run above. Waits for reopen-hydration to settle
  // (same gate as <EcgGraph>'s own mount) so it can't race the persisted
  // state check, and skips a channel whose beats are ALREADY flagged — a
  // reopened session has its arrhythmia results in SQLite and re-running
  // would be wasted work over data the backend already has.
  $effect(() => {
    if (!hydrationChecked) return
    const channel = activeChannel
    if (!channel || autoArrhythmiaChannel === channel) return
    autoArrhythmiaChannel = channel

    void (async () => {
      const state = await refreshBeatsOfInterest()
      if (destroyed || channel !== activeChannel) return
      if (state === 'has-beats' && beatsOfInterest.length === 0) {
        await autoRunArrhythmiaDetection(channel)
      }
    })()
  })

  // The same shared registry row Sidebar renders (see fileRegistry.svelte.ts)
  // — read directly here rather than threaded down as a prop, matching the
  // pattern already used elsewhere for shared session state. A folder
  // import's background auto-run detection (importQueue.svelte.ts) can
  // still be queued, or actively running, by the time a technician opens
  // this file from the sidebar — without watching this row, ReviewWorkspace
  // had no idea and just showed a blank detection summary and empty
  // markers until the technician happened to change channels or click
  // Re-run detection.
  let fileRow = $derived(fileRegistry.find((row) => row.path === path))

  // Reflects the shared registry row's own queued/detecting/detected/error
  // status into the toolbar's detection summary — but ONLY into `detection`
  // fields, never touching `activeChannel`: the backend's beat_cache[path]
  // for the default channel is what the worker in importQueue.svelte.ts is
  // actually populating, and `activeChannel` already starts at
  // `defaultChannel` (see its own declaration above), so no channel
  // advance is needed here the way `handleChannelChange` needs one.
  //
  // Only fires again when `fileRow.status` itself actually CHANGES value —
  // a later manual channel switch or Re-run detection click updates
  // `detection` through their own paths without touching `fileRow.status`
  // at all, so this never clobbers either of those afterward.
  $effect(() => {
    const status = fileRow?.status
    if (status === 'queued' || status === 'detecting') {
      detection = { status: 'pending', count: null, meanHr: null, duration: null, error: null }
    } else if (status === 'detected' && fileRow) {
      detection = {
        status: 'ok',
        count: fileRow.beatCount,
        meanHr: fileRow.meanHr,
        duration: null,
        error: null,
      }
      // The backend's beat_cache[path] just gained real beats — EcgGraph
      // may already be mounted (and showing an empty markers window) if
      // the technician opened this file before detection finished.
      //
      // Deferred one microtask rather than called synchronously here: doing
      // it inline reliably threw Svelte's `effect_update_depth_exceeded` in
      // this exact chain (this effect → `refreshGraphBeats` bumping
      // `beatsRefreshToken` → EcgGraph's own `$effect` on that prop →
      // `refreshBeatsOnly` → `chart.setData`) — every step reads state this
      // effect doesn't, so nothing here is a genuine circular dependency,
      // but Svelte's depth guard still counted the whole synchronous chain
      // against this effect's own budget. A microtask breaks the chain into
      // two independent flushes with no user-visible delay.
      queueMicrotask(() => refreshGraphBeats())
    } else if (status === 'detection-error' && fileRow) {
      detection = { status: 'error', count: null, meanHr: null, duration: null, error: fileRow.error }
    }
  })

  // Set when mount-time hydration found prior work for this file, so the
  // banner can say what was brought back. `null` = started fresh.
  let restored: { reviewedBeats: number; totalBeats: number; marks: number } | null = $state(null)
  let discarding: boolean = $state(false)

  // Throws away the restored session and re-detects from scratch — the
  // escape hatch for "this saved review is wrong, start over".
  async function handleStartFresh() {
    discarding = true
    try {
      const result = await discardFileState(path)
      if (destroyed) return
      if (result.status !== 'ok' || result.error) {
        persistError = result.error ?? 'Could not discard the saved review'
        return
      }
      restored = null
      selectedBeat = null
      beatsOfInterest = []
      typicalRr = null
      lastPersistedChannel = null
      // Let the automatic on-load run fire again for this channel now that
      // there is nothing persisted to inherit.
      autoArrhythmiaChannel = null
      await handleChannelChange()
    } finally {
      if (!destroyed) discarding = false
    }
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
    // A native Save As dialog (not just a directory picker) — the
    // technician chooses BOTH the destination folder and the filename,
    // pre-filled with a sensible default derived from the source file.
    const sourceName = path.split(/[\\/]/).pop() ?? 'report'
    const defaultFileName = `${sourceName.replace(/\.[^./\\]+$/, '')}-report.xlsx`
    const outputPath = await window.api?.pickReportSavePath(defaultFileName)
    if (!outputPath) return // canceled, or no Electron bridge present

    report = { status: 'generating', outputPath: null, error: null }
    const result = await generateReport(path, outputPath)
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
        // Restoring silently means a technician can't tell whether they're
        // continuing prior work or starting over — and can't choose. Both
        // are shown, and can be acted on, via the banner below.
        restored = {
          reviewedBeats: (result.beats ?? []).filter((b) => b.review_state !== 'unreviewed').length,
          totalBeats: (result.beats ?? []).length,
          marks: (result.bad_data_marks ?? []).length,
        }
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

  // A deleted beat is gone from beat_cache and SQLite alike, so the
  // selection it backed is stale and the graph/navigation both need to
  // re-read rather than patch around it.
  function handleBeatDeleted() {
    selectedBeat = null
    refreshGraphBeats()
    void refreshBeatsOfInterest()
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
      // Rejecting a beat clears its flags, so it drops out of the
      // beat-of-interest navigation; confirming or reassigning can change
      // the set too. Re-read rather than trying to patch the list locally.
      void refreshBeatsOfInterest()
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
    await refreshBeatsOfInterest()
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

  // Re-runs beat detection for the currently active channel on demand —
  // e.g. after changing Settings' beat-detection parameters, or just to
  // confirm a result — distinct from `handleChannelChange` above, which
  // only fires on an actual <select> change. Always targets `activeChannel`
  // itself, whatever the <select> currently shows.
  //
  // Mirrors `handleArrhythmiaComplete`'s pattern above: a fresh detection
  // only updates the backend's in-memory `beat_cache[path]`, so the
  // graph's markers (`refreshGraphBeats`) and any already-persisted review
  // state (`persistChannelData`) both need an explicit refresh afterward.
  // And since `activeChannel` isn't actually changing VALUE here, the
  // `$effect` above that normally clears `selectedBeat` on a channel
  // switch won't fire for this — done by hand instead, since a beat
  // selected against the OLD detection result may not even exist in the
  // new one.
  async function handleRerunDetection() {
    const channel = activeChannel
    const requestId = ++detectionRequestId

    detection = { status: 'pending', count: null, meanHr: null, duration: null, error: null }

    const result = await detectBeats(path, channel)
    if (requestId !== detectionRequestId) return // superseded by a channel switch started meanwhile

    if (result.status === 'ok' && !result.error) {
      detection = {
        status: 'ok',
        count: result.count,
        meanHr: result.mean_hr,
        duration: result.duration,
        error: null,
      }
      selectedBeat = null
      persistPending = false
      persistError = null
      refreshGraphBeats()
      await persistChannelData(channel)
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

<div class="review-workspace" data-testid="review-workspace">
  <div class="review-toolbar">
    <select
      class="channel-select"
      data-testid="channel-select"
      bind:value={selectedChannel}
      onchange={handleChannelChange}
      disabled={!hydrationChecked}
    >
      {#each channels as channel}
        <option value={channel}>{channel}</option>
      {/each}
    </select>

    <button
      type="button"
      class="btn btn-sm"
      data-testid="rerun-detection-button"
      disabled={!hydrationChecked || detection.status === 'pending'}
      onclick={handleRerunDetection}
    >
      Re-run detection
    </button>

    {#if detection.status === 'pending'}
      <div class="detection-summary text-muted" data-testid="detection-summary">
        <span class="spinner" aria-hidden="true"></span>
        {#if fileRow?.status === 'queued'}
          beat detection is queued…
        {:else}
          detecting beats for {selectedChannel}…
        {/if}
      </div>
    {:else if detection.status === 'ok'}
      <div class="detection-summary text-muted" data-testid="detection-summary">
        {detection.count} beats detected{detection.meanHr !== null ? `, mean HR ${detection.meanHr}` : ''}{detection.duration !==
        null
          ? `, duration ${detection.duration}s`
          : ''}
      </div>
    {:else if detection.status === 'error'}
      <div class="detection-summary text-danger" data-testid="detection-summary">
        detection failed: {detection.error}
      </div>
    {/if}

    <span class="toolbar-spacer"></span>
  </div>

  {#if restored}
    <div class="restore-banner" data-testid="restored-session-banner">
      <Icon name="check-circle" size={14} />
      <span>
        Picked up where you left off — <strong>{restored.totalBeats}</strong> saved beats,
        <strong>{restored.reviewedBeats}</strong> already reviewed, and
        <strong>{restored.marks}</strong> bad-data {restored.marks === 1 ? 'range' : 'ranges'}.
      </span>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="start-fresh-button"
        title="Discard the saved review for this file and detect again from scratch"
        disabled={discarding}
        onclick={handleStartFresh}
      >
        {discarding ? 'Starting fresh…' : 'Start fresh'}
      </button>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="dismiss-restore-banner"
        aria-label="Dismiss"
        onclick={() => (restored = null)}
      >
        <Icon name="x" size={12} />
      </button>
    </div>
  {/if}

  <div class="arrhythmia-toolbar">
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
      disabled={!hydrationChecked || autoArrhythmiaRunning}
    />
    {#if autoArrhythmiaRunning}
      <span class="text-muted" data-testid="auto-arrhythmia-running">
        Finding beats of interest…
      </span>
    {/if}
  </div>

  <div class="review-main" class:beat-panel-collapsed={beatPanelCollapsed}>
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
      {#if !hydrationChecked}
        <div class="graph-loading" data-testid="graph-loading">
          <span class="spinner" aria-hidden="true"></span>
          Loading…
        </div>
      {/if}
      {#if hydrationChecked}
        {#key activeChannel}
          <EcgGraph
            {path}
            channel={activeChannel}
            onBeatSelect={handleBeatSelect}
            {beatsRefreshToken}
            {initialBadDataMarks}
            selectedBeatTs={selectedBeat?.ts ?? null}
            {beatsOfInterest}
            {typicalRr}
            {focusOverlay}
            onFocusModeChange={(on) => (focusMode = on)}
          />
        {/key}
      {/if}
    </div>

    {#snippet focusOverlay()}
      {#if selectedBeat && !persistPending && !(persistError && lastPersistedChannel !== activeChannel)}
        {#key selectedBeat.ts}
          <BeatCategoryPanel
            {path}
            beat={selectedBeat}
            onUpdated={handleCategoryUpdated}
            onDeleted={handleBeatDeleted}
          />
        {/key}
      {:else}
        <p class="text-muted">Preparing review data…</p>
      {/if}
    {/snippet}

    <div class="beat-panel" class:collapsed={beatPanelCollapsed} data-testid="selected-beat-panel">
      <div class="beat-panel-header">
        <button
          type="button"
          class="icon-btn"
          data-testid="beat-panel-toggle"
          onclick={toggleBeatPanel}
          aria-pressed={beatPanelCollapsed}
          aria-label={beatPanelCollapsed ? 'Expand beat details' : 'Collapse beat details'}
          title={beatPanelCollapsed ? 'Expand beat details' : 'Collapse beat details'}
        >
          <Icon name={beatPanelCollapsed ? 'chevron-left' : 'chevron-right'} size={16} />
        </button>
        {#if !beatPanelCollapsed}<span class="beat-panel-title">Beat details</span>{/if}
      </div>

      {#if !beatPanelCollapsed}
      <div class="beat-panel-content">
        {#if selectedBeat === null}
          <div class="beat-panel-placeholder text-muted" data-testid="selected-beat-summary">
            <p>No beat selected</p>
            <p class="placeholder-hint">
              {#if beatsOfInterest.length > 0}
                Press <kbd>N</kbd> to step to the next beat of interest, or click any marker on the graph.
              {:else}
                Click a marker on the graph to review that beat.
              {/if}
            </p>
          </div>
        {:else if persistPending}
          <p class="text-muted" data-testid="persist-pending">Preparing review data for {activeChannel}…</p>
        {:else if persistError && lastPersistedChannel !== activeChannel}
          <div class="banner banner-error" data-testid="persist-error">
            <p>Could not prepare review data: {persistError}</p>
            <button
              type="button"
              class="btn btn-sm"
              data-testid="persist-retry-button"
              onclick={() => ensureChannelPersisted(activeChannel)}
            >
              Retry
            </button>
          </div>
        {:else if focusMode}
          <p class="text-muted" data-testid="beat-panel-in-focus">Beat details are shown on the graph in focus mode.</p>
        {:else}
          {#key selectedBeat.ts}
            <BeatCategoryPanel
              {path}
              beat={selectedBeat}
              onUpdated={handleCategoryUpdated}
              onDeleted={handleBeatDeleted}
            />
          {/key}
        {/if}
      </div>

      <!-- Pinned to the bottom-right of the beat-detail panel, per explicit
           request — it was easy to miss up in the top toolbar next to the
           channel select. -->
      <div class="beat-panel-footer">
        {#if report.status === 'generating'}
          <span class="report-status text-muted" data-testid="report-generating">Generating report…</span>
        {:else if report.status === 'ok'}
          <p class="report-status text-success" data-testid="report-success">Report saved to {report.outputPath}</p>
        {:else if report.status === 'error'}
          <p class="report-status text-danger" data-testid="report-error">Report generation failed: {report.error}</p>
        {/if}
        <button
          type="button"
          class="btn btn-primary"
          data-testid="generate-report-button"
          disabled={report.status === 'generating'}
          onclick={handleGenerateReport}
        >
          Generate Report
        </button>
      </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .review-workspace {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    height: 100%;
    box-sizing: border-box;
  }

  .placeholder-hint {
    margin-top: var(--space-2);
    font-size: var(--font-size-sm);
    line-height: 1.5;
  }

  .placeholder-hint kbd {
    padding: 0 4px;
    border: 1px solid currentColor;
    border-radius: 3px;
    font-family: inherit;
    font-size: 0.7rem;
  }

  .restore-banner {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-success, #15803d);
    border-radius: var(--radius-sm, 4px);
    background: var(--color-accent-soft, #eef1f5);
    font-size: var(--font-size-sm);
  }

  .restore-banner span {
    flex: 1;
  }

  .review-toolbar {
    display: flex;
    align-items: center;
    gap: var(--space-4);
    flex-wrap: wrap;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: var(--space-3) var(--space-4);
  }

  .channel-select {
    min-width: 160px;
  }

  .detection-summary {
    font-size: var(--font-size-sm);
    white-space: nowrap;
  }

  .toolbar-spacer {
    flex: 1;
  }

  .report-status {
    font-size: var(--font-size-sm);
  }

  .arrhythmia-toolbar {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: var(--space-3) var(--space-4);
  }

  .review-main {
    flex: 1;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 320px;
    gap: var(--space-3);
    min-height: 0;
    transition: grid-template-columns var(--transition-fast, 150ms ease);
  }

  .review-main.beat-panel-collapsed {
    grid-template-columns: minmax(0, 1fr) 48px;
  }

  .graph-area {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: var(--space-3);
    min-width: 0;
    overflow: auto;
  }

  .graph-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    height: 380px;
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
  }

  .spinner {
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid var(--color-border);
    border-top-color: var(--color-accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .beat-panel {
    display: flex;
    flex-direction: column;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    overflow: hidden;
    min-width: 0;
  }

  .beat-panel-header {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-bottom: 1px solid var(--color-border);
  }

  .beat-panel.collapsed .beat-panel-header {
    justify-content: center;
    border-bottom: none;
  }

  .beat-panel-title {
    font-size: var(--font-size-sm);
    font-weight: 600;
  }

  .beat-panel-content {
    flex: 1;
    padding: var(--space-4);
    overflow-y: auto;
  }

  .beat-panel-footer {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4);
    border-top: 1px solid var(--color-border);
  }

  .beat-panel-placeholder {
    font-size: var(--font-size-sm);
  }

  @media (max-width: 1024px) {
    .review-main {
      grid-template-columns: 1fr;
    }
  }
</style>
