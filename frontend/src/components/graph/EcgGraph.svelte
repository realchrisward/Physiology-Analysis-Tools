<script module lang="ts">
  import type { WindowBeat } from '../../lib/api/types'

  export interface BeatSeriesData {
    xs: number[]
    channelY: (number | null)[]
    arrhythmiaY: (number | null)[]
    normalY: (number | null)[]
    unevaluatedY: (number | null)[]
  }

  /**
   * Merges the channel waveform (x/y, already resolution-limited by
   * `getChannelWindow`) with beat markers (ts/r_amplitude, from
   * `getBeatsWindow`) into one uPlot-aligned dataset. uPlot requires every
   * series in a chart to share one x-axis array, but a beat's timestamp
   * rarely lands exactly on a waveform sample — so the shared x-axis here is
   * the union of both sets of x-values (sorted ascending), and each y-array
   * is `null` everywhere its own series has no value at that x. The
   * waveform series is rendered with `spanGaps: true` (see the `series`
   * config below) so the extra x-entries contributed by beat timestamps
   * don't fragment its line into visible gaps.
   *
   * Beats are split into three parallel y-arrays by `any_arrhythmia` so each
   * renders as its own uPlot points-only series with a distinct color/style
   * (arrhythmia-flagged / normal / not-yet-evaluated) — see `markerSeriesConfig`.
   * This function is the exact "series/point data passed to uPlot" the
   * marker tests assert against, exported here so those tests can verify the
   * merge/categorization logic directly rather than reaching into a live
   * uPlot instance's internals.
   */
  export function buildBeatAlignedData(channelX: number[], channelY: number[], beats: WindowBeat[]): BeatSeriesData {
    const channelYByX = new Map<number, number>()
    channelX.forEach((x, i) => channelYByX.set(x, channelY[i]))

    const beatByTs = new Map<number, WindowBeat>()
    for (const beat of beats) beatByTs.set(beat.ts, beat)

    const xsSet = new Set<number>(channelX)
    for (const beat of beats) xsSet.add(beat.ts)
    const xs = Array.from(xsSet).sort((a, b) => a - b)

    const channelYOut: (number | null)[] = []
    const arrhythmiaY: (number | null)[] = []
    const normalY: (number | null)[] = []
    const unevaluatedY: (number | null)[] = []

    for (const x of xs) {
      channelYOut.push(channelYByX.has(x) ? (channelYByX.get(x) ?? null) : null)

      const beat = beatByTs.get(x)
      arrhythmiaY.push(beat && beat.any_arrhythmia === true ? beat.r_amplitude : null)
      normalY.push(beat && beat.any_arrhythmia === false ? beat.r_amplitude : null)
      unevaluatedY.push(beat && beat.any_arrhythmia === null ? beat.r_amplitude : null)
    }

    return { xs, channelY: channelYOut, arrhythmiaY, normalY, unevaluatedY }
  }
</script>

<script lang="ts">
  import { onMount } from 'svelte'
  import uPlot from 'uplot'
  import 'uplot/dist/uPlot.min.css'
  import { getChannelWindow, getBeatsWindow } from '../../lib/api/windowing'
  import { addBadData, deleteBadData } from '../../lib/api/persistence'
  import { themeState } from '../../lib/stores/theme.svelte'
  import Icon from '../shared/Icon.svelte'
  import type { BadDataMark } from '../../lib/api/types'

  let {
    path,
    channel,
    onBeatSelect,
    beatsRefreshToken = 0,
    initialBadDataMarks,
  }: {
    path: string
    channel: string
    onBeatSelect?: (beat: WindowBeat) => void
    // Bumped by a parent (ReviewWorkspace, after a successful arrhythmia
    // re-run) to request a beats-ONLY re-fetch at the currently visible
    // range — no channel-window re-fetch, no viewport change. See the
    // `$effect` below (mirrors the `containerWidth`/`previousWidth`
    // skip-the-initial-run pattern already used in this file) and
    // `refreshBeatsOnly`. Plain increment rather than a boolean/event so a
    // rapid second re-run request while the graph is still applying the
    // first one is never silently swallowed — each distinct value the
    // effect observes triggers its own re-fetch.
    beatsRefreshToken?: number
    // F5's reopen-hydration seed: a parent (ReviewWorkspace, after a
    // successful `GET /files/state`) passes the file's previously-persisted
    // bad-data marks here so they render immediately instead of the
    // technician seeing an empty marks bar until a fresh drag adds one.
    // Read once, at component init, into `badDataMarks` below — like
    // `initialChannel` in ReviewWorkspace.svelte, this deliberately does not
    // stay in sync with the prop afterwards (this component's own
    // add/remove handlers become the sole source of truth for
    // `badDataMarks` from mount onward).
    initialBadDataMarks?: BadDataMark[]
  } = $props()

  const DEBOUNCE_MS = 150
  const HEIGHT = 320
  const EXPANDED_HEIGHT = 600
  const ZOOM_FACTOR = 0.75
  // A pan/zoom re-fetch loads a window PAD_FACTOR times wider on EACH side
  // than what's actually visible (so 1 = 3x the visible width total), at a
  // matching higher point resolution. Most everyday panning then requires
  // no fetch at all — the data is already loaded, just outside the
  // currently-visible slice of the x-scale, and simply comes into view as
  // uPlot's own scale window moves; only a pan/zoom that reaches past this
  // buffer (or zooms in deep enough that the buffer's point density is now
  // too coarse) triggers a real fetch. See `needsRefetch`/`maybeBufferAhead`.
  const PAD_FACTOR = 1
  // Refetch once the visible range gets within this fraction of the loaded
  // buffer's edge, rather than waiting until it's exactly exceeded — a
  // small safety margin so the buffer extends slightly before the
  // technician can actually see its edge.
  const BUFFER_MARGIN_RATIO = 0.1
  // Refetch (for resolution, not coverage) once the visible range has
  // shrunk to less than this fraction of the width that was requested when
  // the current buffer was loaded — i.e. "zoomed in more than 3x since".
  const MIN_ZOOM_RATIO_BEFORE_REFETCH = 3
  // A `mousedown`→`mouseup` pair whose largest single-axis pixel
  // displacement stays under this is treated as a click rather than a pan
  // drag (see `handleDragStart`'s `onMove`/`onUp`) — 5px comfortably
  // absorbs incidental hand tremor/trackpad jitter on an intended click
  // without meaningfully delaying real pan gestures (which routinely move
  // tens/hundreds of pixels).
  const CLICK_DRAG_THRESHOLD_PX = 5
  // How close (in screen pixels, converted to data-space via the same
  // `unitsPerPx` uPlot conversion the pan drag already uses) a click needs
  // to land to a beat's `ts` to select it. Deliberately x-only — beat
  // markers are dense along x but sparse in y relative to the waveform, so
  // nearest-by-x-distance is enough to disambiguate without also comparing
  // the click's y/`r_amplitude` distance.
  const BEAT_HIT_TOLERANCE_PX = 8

  // Marker/axis/grid colors. Read from tokens.css's semantic custom
  // properties (falling back to their light-mode values — jsdom in tests
  // never resolves custom properties via getComputedStyle). Canvas
  // fillStyle needs a literal color, not a `var(...)` reference, hence
  // resolving via `cssVar` rather than passing a CSS variable string into
  // uPlot's series config.
  //
  // Each color is wrapped in a zero-arg function rather than resolved once
  // to a plain string constant — uPlot accepts a function for series
  // stroke/fill and axis stroke/grid/ticks (re-evaluated on every redraw),
  // so these stay live across a runtime theme change (light/dark/black &
  // white — see lib/stores/theme.svelte.ts) instead of freezing whatever
  // the OS/attribute happened to be at chart-construction time. The
  // `$effect` below calls `chart.redraw()` whenever the theme changes so
  // that re-evaluation actually happens.
  function cssVar(name: string, fallback: string): string {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return value || fallback
  }
  // Converts a `#rgb`/`#rrggbb` hex color to an `rgba(...)` string so an
  // alpha channel can be applied to it — canvas fillStyle needs a literal
  // RGB(A) triplet, not a bare hex string with alpha bolted on. tokens.css's
  // `--color-text-muted` is always hex in every theme, so that's the only
  // shape this needs to handle; anything else is returned unchanged rather
  // than mangled.
  function hexToRgba(hex: string, alpha: number): string {
    const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex)
    if (!match) return hex
    let value = match[1]
    if (value.length === 3) {
      value = value
        .split('')
        .map((c) => c + c)
        .join('')
    }
    const r = parseInt(value.slice(0, 2), 16)
    const g = parseInt(value.slice(2, 4), 16)
    const b = parseInt(value.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  const waveformColor = () => cssVar('--color-waveform', '#15803d')
  const arrhythmiaBeatColor = () => cssVar('--color-danger', '#b91c1c')
  const normalBeatColor = () => cssVar('--color-accent', '#2563eb')
  // "Not yet evaluated" (any_arrhythmia: null) gets a dimmed treatment:
  // whatever `--color-text-muted` resolves to for the active theme, at
  // reduced opacity.
  const unevaluatedBeatColor = () => hexToRgba(cssVar('--color-text-muted', '#5b6b7c'), 0.45)
  const axisColor = () => cssVar('--color-text-muted', '#5b6b7c')
  const gridColor = () => cssVar('--color-graph-grid', 'rgba(211, 218, 225, 0.6)')

  function markerSeriesConfig(colorFn: () => string): uPlot.Series {
    return {
      paths: () => null,
      points: { show: true, size: 8, width: 1, stroke: () => colorFn(), fill: () => colorFn() },
    }
  }

  // True whenever the visible x-range differs from `fullExtent` (a pan,
  // zoom, or Reset click having happened) — drives the Reset view button's
  // highlighted state below, so it's obvious at a glance that the view has
  // moved from the default and a reset is available.
  let viewChanged: boolean = $state(false)
  // Toggled by the maximize/minimize button in the toolbar; taller chart
  // height for a closer look at dense waveforms. See the `$effect` below
  // that applies it to the live chart.
  let expanded: boolean = $state(false)

  let containerEl: HTMLDivElement | undefined = $state()
  // Tracked via `bind:clientWidth` below. Read directly off `containerEl`
  // for the one-off initial-mount fetch instead (see onMount) so that fetch
  // isn't racing this binding's own first effect run.
  let containerWidth: number = $state(0)
  let error: string | null = $state(null)

  // Bad-data marking. `badDataMode` gates handleDragStart below: off (the
  // default) a drag pans the graph exactly as before; on, the same drag
  // instead selects a range to send to `addBadData`. Marks are seeded from
  // `initialBadDataMarks` on mount (F5's `GET /files/state` reopen flow, via
  // ReviewWorkspace — see that prop's own comment below) and otherwise kept
  // as session-local state, added/removed only through this component's own
  // handlers — rendered as a small proportional strip beneath the graph
  // (`markLeftPct`/`markWidthPct`, relative to the full file extent) rather
  // than as a canvas overlay synced to the current pan/zoom window: it's
  // simpler, and a technician can see/remove a mark regardless of which part
  // of the recording is currently in view.
  let badDataMode: boolean = $state(false)
  // Seeded from `initialBadDataMarks` (F5 reopen hydration) instead of
  // always starting empty — see that prop's own comment above for why this
  // is a one-time read, not an ongoing sync.
  let badDataMarks: BadDataMark[] = $state(initialBadDataMarks ?? [])

  // Plain (non-reactive) instance state: the live uPlot instance, the
  // full-extent range for "Reset view", and the pan/zoom debounce timer.
  // None of these need to drive a re-render themselves — they're read from
  // event handlers and effects, not the template.
  let chart: uPlot | undefined
  let fullExtent: { start: number; end: number } | undefined
  let debounceTimer: ReturnType<typeof setTimeout> | undefined
  // Set on the first `containerWidth` effect run (the binding's own initial
  // read), so later effect runs can tell an actual resize apart from that.
  let previousWidth: number | undefined
  // Teardown for the currently-active drag's `document`-level listeners, if
  // any drag is in progress. Reachable from both the drag's own `onUp`
  // completion path and the component's onMount/onDestroy cleanup, so a
  // drag can never outlive the component (e.g. if it unmounts mid-drag —
  // `{#key selectedChannel}` remount, or navigating away — the listeners
  // get removed instead of lingering on `document` forever).
  let endActiveDrag: (() => void) | undefined
  // The raw `WindowBeat[]` from the most recent successful `getBeatsWindow`
  // call (set in `loadBeats` below), kept alongside — not instead of —
  // `buildBeatAlignedData`'s merged series: the merged series only carries
  // `ts`/`r_amplitude` (the two fields uPlot needs to render markers), but a
  // click-to-select hit-test needs the beat's full record (arrhythmia
  // flags, `hr`, etc.) to hand to `onBeatSelect`. Not `$state` — like
  // `chart`/`fullExtent` above, it's only ever read from an event handler,
  // never from the template.
  let lastBeats: WindowBeat[] = []
  // The raw channel {x, y} from the most recent successful `loadWindow` call
  // (set in `fetchMergedWindow` below), retained for the same reason as
  // `lastBeats` above but for `refreshBeatsOnly`'s benefit: a beats-only
  // re-fetch (triggered by `beatsRefreshToken`, see its `$effect` below)
  // must re-merge fresh beats against the waveform data currently on
  // screen without re-fetching `/channels/window` at all — this is exactly
  // that waveform data, always in sync with what's rendered since it's only
  // ever updated alongside the `chart.setData`/construction calls that
  // display it.
  let lastChannelWindow: { x: number[]; y: number[] } | undefined
  // Set once the first `beatsRefreshToken` effect run has captured its
  // starting value — see that `$effect` for why the first run must be a
  // no-op (same discipline as `previousWidth` above).
  let previousBeatsRefreshToken: number | undefined

  // The x-range actually LOADED into the chart right now (normally wider
  // than what's visible — see PAD_FACTOR below), and the visible-range
  // width that was requested when it was fetched (used to detect "zoomed
  // in enough since then that this buffer's point density is now too
  // coarse"). `undefined` until the first fetch (mount) completes.
  let loadedRange: { start: number; end: number; requestedWidth: number } | undefined
  // Guards `maybeBufferAhead` against firing overlapping fetches for
  // (approximately) the same range on consecutive mousemove ticks during a
  // single fast drag — a request-frequency throttle, independent of
  // `refetchRequestId` below (which guards response *correctness*, not
  // frequency).
  let bufferFetchInFlight = false
  // Bumped at the start of every `refetch` call (pan/zoom/reset alike) and
  // captured locally by each call; a resolution only applies itself if its
  // captured id still matches the live counter. Without this, a
  // pan/zoom's already-in-flight fetch (past its debounce, awaiting the
  // network) that is still outstanding when Reset View is clicked would —
  // having no way to be cancelled — resolve *after* the reset and clobber
  // the just-reset chart with the stale pre-reset window's data, leaving
  // the scale pinned to the full extent but the actual waveform occupying
  // only the old, much narrower range within it (the reported "reset
  // zooms out way too much" bug — fixed by a second click only because, by
  // then, no older in-flight fetch remained to race it).
  let refetchRequestId = 0

  function resolutionFor(width: number): number {
    return Math.max(1, Math.round(width))
  }

  async function loadWindow(start: number, end: number, width: number): Promise<{ x: number[]; y: number[] } | null> {
    const result = await getChannelWindow(path, channel, start, end, resolutionFor(width))
    if (result.status !== 'ok' || result.error) {
      error = result.error ?? 'Failed to load channel window'
      return null
    }
    error = null
    return { x: result.x, y: result.y }
  }

  // Beats are fetched right after the channel window, on the same
  // range-change (initial load and every pan/zoom re-fetch) — sequentially,
  // not concurrently, so the two requests have a deterministic order (channel
  // first, beats second). A beats-fetch failure never blocks rendering the
  // waveform itself; it just means no marker overlay for that range, so it
  // doesn't set `error` the way a channel-window failure does.
  async function loadBeats(start: number, end: number): Promise<WindowBeat[]> {
    const result = await getBeatsWindow(path, start, end)
    if (result.status !== 'ok' || result.error || !Array.isArray(result.beats)) {
      lastBeats = []
      return []
    }
    lastBeats = result.beats
    return result.beats
  }

  async function fetchMergedWindow(
    start: number,
    end: number,
    width: number,
  ): Promise<{ channel: { x: number[]; y: number[] }; merged: BeatSeriesData } | null> {
    const channelData = await loadWindow(start, end, width)
    if (!channelData) return null
    lastChannelWindow = channelData
    const beats = await loadBeats(start, end)
    return { channel: channelData, merged: buildBeatAlignedData(channelData.x, channelData.y, beats) }
  }

  // Re-fetches ONLY `/beats/window` for the currently visible x-scale range
  // and re-merges it against `lastChannelWindow` (the waveform data already
  // on screen) — deliberately not `fetchMergedWindow`/`refetch`, which also
  // re-fetch `/channels/window` and are used by the pan/zoom/reset-view
  // paths. Triggered by `beatsRefreshToken` (see its `$effect` below) after
  // e.g. a successful arrhythmia re-run, where only the beats' arrhythmia
  // flags have changed, not the waveform or the technician's current
  // pan/zoom position.
  async function refreshBeatsOnly() {
    if (!chart || !lastChannelWindow) return
    const { min, max } = chart.scales.x
    if (min == null || max == null) return
    const beats = await loadBeats(min, max)
    const merged = buildBeatAlignedData(lastChannelWindow.x, lastChannelWindow.y, beats)
    chart.setData([merged.xs, merged.channelY, merged.arrhythmiaY, merged.normalY, merged.unevaluatedY], false)
  }

  // True once the visible [min, max] range has moved close enough to (or
  // past) the loaded buffer's edge, or has been zoomed in deep enough
  // relative to the buffer's own resolution, that a real fetch is needed.
  // `false` means the chart already has everything it needs to show this
  // range correctly — no fetch, no loading delay.
  function needsRefetch(min: number, max: number): boolean {
    if (!loadedRange) return true
    const visibleWidth = max - min
    const margin = visibleWidth * BUFFER_MARGIN_RATIO
    const withinBounds = min >= loadedRange.start + margin && max <= loadedRange.end - margin
    if (!withinBounds) return true
    return visibleWidth * MIN_ZOOM_RATIO_BEFORE_REFETCH < loadedRange.requestedWidth
  }

  // `pad: false` (used only by Reset View) fetches exactly [min, max] with
  // no buffer — Reset already targets the maximal legitimate range, so
  // padding it further would just over-request with no benefit. Every
  // other caller pads (see PAD_FACTOR).
  async function refetch(min: number, max: number, options: { pad?: boolean } = {}) {
    const requestId = ++refetchRequestId
    const usePad = options.pad ?? true
    const requestedWidth = max - min
    const pad = usePad ? requestedWidth * PAD_FACTOR : 0
    const fetchStart = min - pad
    const fetchEnd = max + pad
    const fetchWidth = usePad ? containerWidth * (1 + 2 * PAD_FACTOR) : containerWidth

    const result = await fetchMergedWindow(fetchStart, fetchEnd, fetchWidth)
    // Superseded by a newer refetch (a later pan/zoom, or a Reset) dispatched
    // while this one was in flight — see `refetchRequestId`'s own comment
    // above for why applying a stale response here would be a real bug
    // (the "reset zooms out too much until a second click" report).
    if (requestId !== refetchRequestId) return

    if (result && chart) {
      const { merged } = result
      chart.setData([merged.xs, merged.channelY, merged.arrhythmiaY, merged.normalY, merged.unevaluatedY], false)
      loadedRange = { start: fetchStart, end: fetchEnd, requestedWidth }
    }
  }

  function scheduleRefetch(min: number, max: number) {
    if (debounceTimer !== undefined) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      debounceTimer = undefined
      if (!needsRefetch(min, max)) return // buffer already covers this range at an adequate resolution
      void refetch(min, max)
    }, DEBOUNCE_MS)
  }

  // Immediate (non-debounced) buffer-ahead fetch, called on every live-pan
  // mousemove tick (and every wheel/button zoom step) in addition to the
  // debounced `scheduleRefetch` above. Without this, a single long,
  // continuous drag would show blank chart past the edge of whatever was
  // loaded before the drag started, filling in only once the drag pauses
  // or ends — `scheduleRefetch`'s 150ms debounce keeps getting reset by
  // mousemove events that fire every ~16ms during continuous motion, so it
  // alone never gets a chance to fire mid-drag. This fires the moment the
  // buffer is actually exceeded, so the newly-visible area is (mostly)
  // already loaded well before the technician can see its edge — throttled
  // by `bufferFetchInFlight` so a fast drag doesn't fire overlapping
  // requests for approximately the same range.
  function maybeBufferAhead(min: number, max: number) {
    if (bufferFetchInFlight || !needsRefetch(min, max)) return
    bufferFetchInFlight = true
    void refetch(min, max).finally(() => {
      bufferFetchInFlight = false
    })
  }

  async function submitBadDataMark(start: number, stop: number) {
    const result = await addBadData(path, start, stop)
    if (result.status === 'ok' && result.mark && !result.error) {
      badDataMarks = [...badDataMarks, result.mark]
    } else {
      error = result.error ?? 'Failed to add bad data mark'
    }
  }

  async function removeBadDataMark(mark: BadDataMark) {
    const result = await deleteBadData(path, mark.id)
    if (result.status === 'ok' && !result.error) {
      badDataMarks = badDataMarks.filter((m) => m.id !== mark.id)
    } else {
      error = result.error ?? 'Failed to remove bad data mark'
    }
  }

  function toggleBadDataMode() {
    badDataMode = !badDataMode
  }

  // While bad-data mode is active, a left-drag on the plot selects a range
  // to mark instead of panning — the two behaviors are mutually exclusive
  // per drag, gated by `badDataMode` at drag-start. Uses the same
  // `endActiveDrag` teardown slot as the pan drag below (see its own
  // discipline note), so onMount/onDestroy cleanup tears this drag down too
  // if the component unmounts mid-drag.
  function handleBadDataDragStart(u: uPlot, downEvent: MouseEvent) {
    const rect = u.over.getBoundingClientRect()
    const startVal = u.posToVal(downEvent.clientX - rect.left, 'x')

    function detach() {
      document.removeEventListener('mousemove', noopMove)
      document.removeEventListener('mouseup', onUp)
      endActiveDrag = undefined
    }

    // No live visual feedback during a bad-data selection drag (unlike pan,
    // which redraws on every mousemove) — the contract is just that a mark
    // is added on release. The listener still needs to exist so `detach()`
    // has something to remove.
    function noopMove() {}

    function onUp(upEvent: MouseEvent) {
      detach()
      const stopVal = u.posToVal(upEvent.clientX - rect.left, 'x')
      void submitBadDataMark(startVal, stopVal)
    }

    document.addEventListener('mousemove', noopMove)
    document.addEventListener('mouseup', onUp)
    endActiveDrag = detach
  }

  // Finds the beat in the retained `lastBeats` (see its declaration above)
  // whose `ts` is closest to `xVal`, within `toleranceVal` data-units —
  // both already converted from screen pixels by the caller. Returns
  // `undefined` if no beat falls within tolerance.
  function findNearestBeat(xVal: number, toleranceVal: number): WindowBeat | undefined {
    let nearest: WindowBeat | undefined
    let nearestDist = Infinity
    for (const candidate of lastBeats) {
      const dist = Math.abs(candidate.ts - xVal)
      if (dist <= toleranceVal && dist < nearestDist) {
        nearest = candidate
        nearestDist = dist
      }
    }
    return nearest
  }

  // Called from `handleDragStart`'s `onUp` once a `mousedown`→`mouseup`
  // pair has been classified as a click (see `CLICK_DRAG_THRESHOLD_PX`),
  // never for an actual pan drag or while bad-data mode is active (bad-data
  // mode's own drag handling in `handleBadDataDragStart` is untouched by
  // this feature — see that function's own comment). Converts the click's
  // pixel position to data-space via `u.posToVal`, the same
  // coordinate-conversion API the pan/zoom/bad-data-mark code above already
  // uses, then hit-tests it against the retained beats.
  function handleBeatClick(u: uPlot, upEvent: MouseEvent) {
    if (!onBeatSelect) return
    const rect = u.over.getBoundingClientRect()
    const xVal = u.posToVal(upEvent.clientX - rect.left, 'x')
    const unitsPerPx = u.posToVal(1, 'x') - u.posToVal(0, 'x')
    const toleranceVal = Math.abs(unitsPerPx) * BEAT_HIT_TOLERANCE_PX
    const beat = findNearestBeat(xVal, toleranceVal)
    if (beat) onBeatSelect(beat)
  }

  // Left-drag (no modifier) pans: mutates the x-scale directly on every
  // mousemove for immediate visual feedback, then schedules one debounced
  // re-fetch once the drag ends. A THIRD outcome shares this same
  // mousedown/mousemove/mouseup sequence (rather than a second, parallel
  // listener setup): if the total movement between mousedown and mouseup
  // never reaches `CLICK_DRAG_THRESHOLD_PX`, nothing pans (the x-scale is
  // never mutated at all — see `onMove` below, which only starts calling
  // `setScale` once the threshold is actually crossed) and the gesture is
  // instead treated as a click-to-select via `handleBeatClick`. This mirrors
  // `handleBadDataDragStart`'s existing mutual exclusion with this branch
  // (gated by `badDataMode` below) rather than introducing a fourth,
  // independent code path.
  function handleDragStart(u: uPlot, downEvent: MouseEvent) {
    if (downEvent.button !== 0) return
    downEvent.preventDefault()

    if (badDataMode) {
      handleBadDataDragStart(u, downEvent)
      return
    }

    const startX = downEvent.clientX
    const startY = downEvent.clientY
    const scaleMin0 = u.scales.x.min ?? 0
    const scaleMax0 = u.scales.x.max ?? 0
    const unitsPerPx = u.posToVal(1, 'x') - u.posToVal(0, 'x')
    // Largest single-axis pixel displacement seen so far this gesture —
    // compared against `CLICK_DRAG_THRESHOLD_PX` in `onMove`/`onUp` to tell
    // "the user panned" from "the user clicked without (meaningfully)
    // moving the mouse".
    let maxMovementPx = 0

    function onMove(moveEvent: MouseEvent) {
      maxMovementPx = Math.max(
        maxMovementPx,
        Math.abs(moveEvent.clientX - startX),
        Math.abs(moveEvent.clientY - startY),
      )
      // Below the threshold, don't touch the scale at all — so a gesture
      // that ends up classified as a click (in onUp) never leaves behind an
      // imperceptible-but-real pan that the click branch's "don't refetch"
      // path would otherwise leave desynced from the displayed data.
      if (maxMovementPx < CLICK_DRAG_THRESHOLD_PX) return
      const dx = unitsPerPx * (moveEvent.clientX - startX)
      const newMin = scaleMin0 - dx
      const newMax = scaleMax0 - dx
      u.setScale('x', { min: newMin, max: newMax })
      maybeBufferAhead(newMin, newMax)
    }

    function detach() {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      endActiveDrag = undefined
    }

    function onUp(upEvent: MouseEvent) {
      detach()
      if (maxMovementPx < CLICK_DRAG_THRESHOLD_PX) {
        handleBeatClick(u, upEvent)
        return
      }
      viewChanged = true
      const { min, max } = u.scales.x
      if (min != null && max != null) scheduleRefetch(min, max)
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    endActiveDrag = detach
  }

  // Wheel zooms in/out centered on the cursor's x position, then schedules a
  // debounced re-fetch of the new visible range.
  function handleWheel(u: uPlot, wheelEvent: WheelEvent) {
    wheelEvent.preventDefault()

    const rect = u.over.getBoundingClientRect()
    const left = wheelEvent.clientX - rect.left
    // jsdom never lays elements out (getBoundingClientRect is always
    // zeroed), so rect.width is 0 in tests regardless of the mocked
    // clientWidth used to size the chart itself — fall back to centering
    // rather than dividing by zero.
    const leftPct = rect.width === 0 ? 0.5 : left / rect.width
    const xVal = u.posToVal(left, 'x')
    const oldMin = u.scales.x.min ?? 0
    const oldMax = u.scales.x.max ?? 0
    const oldRange = oldMax - oldMin

    const newRange = wheelEvent.deltaY < 0 ? oldRange * ZOOM_FACTOR : oldRange / ZOOM_FACTOR
    const newMin = xVal - leftPct * newRange
    const newMax = newMin + newRange

    // Wheel/trackpad zoom deliberately stays on the debounced path only
    // (no `maybeBufferAhead`) — a pinch/scroll gesture can fire many wheel
    // events per second, and firing an immediate, non-debounced fetch on
    // every one of them would spam the backend; `scheduleRefetch`'s 150ms
    // debounce already coalesces a rapid burst into one request, which is
    // imperceptible for a single zoom gesture.
    viewChanged = true
    u.setScale('x', { min: newMin, max: newMax })
    scheduleRefetch(newMin, newMax)
  }

  // Zoom in/out buttons: same `ZOOM_FACTOR` step as the wheel handler
  // above, centered on the current view's midpoint instead of a cursor
  // position (there's no pointer position to center on for a button click).
  // Same debounced-only reasoning as `handleWheel` above (no
  // `maybeBufferAhead`) — a discrete click doesn't need it, and it would
  // spam repeated clicks the same way a wheel burst would.
  function zoomBy(factor: number) {
    if (!chart) return
    const { min, max } = chart.scales.x
    if (min == null || max == null) return
    const mid = (min + max) / 2
    const newRange = (max - min) * factor
    const newMin = mid - newRange / 2
    const newMax = mid + newRange / 2

    viewChanged = true
    chart.setScale('x', { min: newMin, max: newMax })
    scheduleRefetch(newMin, newMax)
  }

  function handleZoomIn() {
    zoomBy(ZOOM_FACTOR)
  }

  function handleZoomOut() {
    zoomBy(1 / ZOOM_FACTOR)
  }

  function toggleExpanded() {
    expanded = !expanded
  }

  function panZoomPlugin(): uPlot.Plugin {
    return {
      hooks: {
        ready: (u) => {
          u.over.addEventListener('mousedown', (e) => handleDragStart(u, e))
          u.over.addEventListener('wheel', (e) => handleWheel(u, e))
        },
      },
    }
  }

  onMount(() => {
    let cancelled = false

    void (async () => {
      const width = containerEl?.clientWidth ?? 0
      const result = await fetchMergedWindow(0, Number.MAX_SAFE_INTEGER, width)
      if (cancelled || !result || !containerEl) return
      const { channel: data, merged } = result

      fullExtent = { start: data.x[0] ?? 0, end: data.x[data.x.length - 1] ?? 0 }
      // The initial mount fetch already loads the whole file's extent (see
      // the `0, Number.MAX_SAFE_INTEGER` call above) — seeding `loadedRange`
      // from it means a subsequent zoom-in is immediately recognized as
      // needing a real fetch (via `needsRefetch`'s resolution check) with
      // no separate null-case special-casing needed.
      loadedRange = { start: fullExtent.start, end: fullExtent.end, requestedWidth: fullExtent.end - fullExtent.start }

      chart = new uPlot(
        {
          width,
          height: expanded ? EXPANDED_HEIGHT : HEIGHT,
          scales: { x: { time: false } },
          // uPlot's own default cursor behavior is a click-drag rubber-band
          // select that zooms into the selected x-range on mouseup
          // (`cursor.drag` defaults to `{ setScale: true, x: true, dist: 0
          // }`) — left enabled, it fires *in addition to* our own manual
          // pan/click/bad-data-drag handlers below (attached directly to
          // `u.over` in `panZoomPlugin`), competing with them: a plain pan
          // drag would visibly pan live via our `setScale` calls, then get
          // stomped by uPlot's own built-in zoom-to-selection on release,
          // and Reset View's `setScale` could likewise be fought by
          // leftover cursor/select state from a prior drag. Disabled
          // entirely (`x`/`y`/`setScale` all false) since our own listeners
          // are the sole intended source of scale changes.
          cursor: { drag: { x: false, y: false, setScale: false } },
          axes: [
            { stroke: () => axisColor(), grid: { stroke: () => gridColor(), width: 1 }, ticks: { stroke: () => axisColor() } },
            { stroke: () => axisColor(), grid: { stroke: () => gridColor(), width: 1 }, ticks: { stroke: () => axisColor() } },
          ],
          series: [
            {},
            { stroke: () => waveformColor(), width: 1.5, spanGaps: true },
            markerSeriesConfig(arrhythmiaBeatColor),
            markerSeriesConfig(normalBeatColor),
            markerSeriesConfig(unevaluatedBeatColor),
          ],
          plugins: [panZoomPlugin()],
        },
        [merged.xs, merged.channelY, merged.arrhythmiaY, merged.normalY, merged.unevaluatedY],
        containerEl,
      )
    })()

    return () => {
      cancelled = true
      if (debounceTimer !== undefined) clearTimeout(debounceTimer)
      // A drag in progress attaches its mousemove/mouseup listeners to
      // `document`, not to uPlot's own root node — `chart.destroy()` below
      // only removes uPlot's own DOM/listeners, so an active drag must be
      // torn down separately or it outlives the component.
      endActiveDrag?.()
      chart?.destroy()
      chart = undefined
    }
  })

  // Reacts to `bind:clientWidth` changes on the container (window
  // resizes/layout changes). The binding's own effect fires once
  // synchronously on mount with the starting width — recorded via
  // `previousWidth` but not treated as a resize, since the initial chart
  // construction above already used that same width.
  $effect(() => {
    const width = containerWidth
    if (previousWidth === undefined) {
      previousWidth = width
      return
    }
    if (width === previousWidth) return
    previousWidth = width

    if (!chart) return
    chart.setSize({ width, height: expanded ? EXPANDED_HEIGHT : HEIGHT })
    const { min, max } = chart.scales.x
    if (min != null && max != null) scheduleRefetch(min, max)
  })

  // Applies the maximize/minimize toggle to the live chart. Guarded on
  // `chart` existing (it may still be mid-construction on the very first
  // run, before `expanded` could plausibly have changed from its initial
  // `false`) rather than needing to coordinate with the async onMount setup.
  $effect(() => {
    const isExpanded = expanded
    if (!chart) return
    chart.setSize({ width: containerWidth, height: isExpanded ? EXPANDED_HEIGHT : HEIGHT })
  })

  // Colors above are resolved via zero-arg functions specifically so this
  // effect can make them live: a runtime theme change (see
  // lib/stores/theme.svelte.ts) doesn't itself touch the chart, so without
  // an explicit redraw the canvas would keep showing whatever colors were
  // last resolved. `redraw(true, true)` forces uPlot to re-run every
  // series/axis stroke/fill/grid function against the new computed styles.
  $effect(() => {
    themeState.mode
    chart?.redraw(true, true)
  })

  // Mirrors the `containerWidth`/`previousWidth` effect above: the binding
  // this reads (`beatsRefreshToken`, a prop) fires once synchronously on
  // mount with its starting value — recorded via `previousBeatsRefreshToken`
  // but not treated as a refresh request, since the initial chart
  // construction in `onMount` already fetched beats for that value. Only a
  // later, *different* value (the parent incrementing it) triggers
  // `refreshBeatsOnly`.
  $effect(() => {
    const token = beatsRefreshToken
    if (previousBeatsRefreshToken === undefined) {
      previousBeatsRefreshToken = token
      return
    }
    if (token === previousBeatsRefreshToken) return
    previousBeatsRefreshToken = token
    void refreshBeatsOnly()
  })

  function handleResetView() {
    if (!chart || !fullExtent) return
    if (debounceTimer !== undefined) {
      clearTimeout(debounceTimer)
      debounceTimer = undefined
    }
    viewChanged = false
    chart.setScale('x', { min: fullExtent.start, max: fullExtent.end })
    void refetch(fullExtent.start, fullExtent.end, { pad: false })
  }

  // Positions a bad-data mark within the marks bar as a percentage of the
  // full file extent (not the currently panned/zoomed window) — see the
  // `badDataMarks` declaration above for why. By the time any mark can
  // exist, a drag on the plot must have happened, which means `fullExtent`
  // is already set (it's assigned before the chart — and therefore before
  // any drag is possible — in onMount), so no reactivity on `fullExtent`
  // itself is needed for these to stay correct.
  function markLeftPct(mark: BadDataMark): number {
    if (!fullExtent) return 0
    const range = fullExtent.end - fullExtent.start
    if (range <= 0) return 0
    const left = Math.min(mark.start, mark.stop)
    return Math.max(0, Math.min(100, ((left - fullExtent.start) / range) * 100))
  }

  function markWidthPct(mark: BadDataMark): number {
    if (!fullExtent) return 0
    const range = fullExtent.end - fullExtent.start
    if (range <= 0) return 0
    const width = Math.abs(mark.stop - mark.start)
    // Floor of 0.5% so a very narrow mark stays visible/clickable.
    return Math.max(0.5, Math.min(100, (width / range) * 100))
  }
</script>

<div class="ecg-graph" data-testid="ecg-graph" data-channel={channel}>
  <div class="ecg-graph-toolbar">
    <button type="button" class="btn btn-sm" data-testid="zoom-in-button" title="Zoom in" onclick={handleZoomIn}>
      <Icon name="zoom-in" size={14} />
    </button>
    <button type="button" class="btn btn-sm" data-testid="zoom-out-button" title="Zoom out" onclick={handleZoomOut}>
      <Icon name="zoom-out" size={14} />
    </button>
    <button
      type="button"
      class="btn btn-sm"
      class:btn-active={viewChanged}
      data-testid="reset-view-button"
      title="Reset view"
      onclick={handleResetView}
    >
      <Icon name="refresh-cw" size={14} /> Reset view
    </button>
    <button
      type="button"
      class="btn btn-sm"
      class:btn-active={badDataMode}
      data-testid="bad-data-mode-button"
      aria-pressed={badDataMode}
      onclick={toggleBadDataMode}
    >
      <Icon name="crop" size={14} />
      {badDataMode ? 'Exit bad data mode' : 'Mark bad data'}
    </button>
    <button
      type="button"
      class="btn btn-sm"
      data-testid="expand-graph-button"
      title={expanded ? 'Restore graph size' : 'Enlarge graph'}
      aria-pressed={expanded}
      onclick={toggleExpanded}
    >
      <Icon name={expanded ? 'minimize' : 'maximize'} size={14} />
    </button>
  </div>

  {#if error}
    <div class="banner banner-error" data-testid="ecg-graph-error">{error}</div>
  {/if}

  <div class="ecg-graph-container" data-testid="ecg-graph-container" bind:this={containerEl} bind:clientWidth={containerWidth}></div>

  <div data-testid="bad-data-marks-bar" class="bad-data-marks-bar">
    {#each badDataMarks as mark (mark.id)}
      <button
        type="button"
        data-testid="bad-data-mark"
        class="bad-data-mark"
        style={`left: ${markLeftPct(mark)}%; width: ${markWidthPct(mark)}%`}
        onclick={() => removeBadDataMark(mark)}
        title={`Bad data ${mark.start.toFixed(2)}–${mark.stop.toFixed(2)} (click to remove)`}
        aria-label={`Bad data mark from ${mark.start.toFixed(2)} to ${mark.stop.toFixed(2)}, click to remove`}
      ></button>
    {/each}
  </div>
</div>

<style>
  .ecg-graph {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .ecg-graph-toolbar {
    display: flex;
    gap: var(--space-2);
    align-self: flex-end;
  }

  .btn-active {
    background: var(--color-accent-soft, #eef1f5);
    border-color: var(--color-accent, #2563eb);
    color: var(--color-accent, #2563eb);
  }

  .ecg-graph-container {
    width: 100%;
  }

  .bad-data-marks-bar {
    position: relative;
    height: var(--space-3, 12px);
    margin-top: var(--space-2, 8px);
    background: var(--color-border, #d3dae1);
    border-radius: var(--radius-sm, 4px);
    overflow: hidden;
  }

  .bad-data-mark {
    position: absolute;
    top: 0;
    bottom: 0;
    background: var(--color-warning, #b45309);
    border: none;
    padding: 0;
    cursor: pointer;
  }
</style>
