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
  import type { BadDataMark } from '../../lib/api/types'

  let {
    path,
    channel,
    onBeatSelect,
    beatsRefreshToken = 0,
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
  } = $props()

  const DEBOUNCE_MS = 150
  const HEIGHT = 320
  const ZOOM_FACTOR = 0.75
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

  // Marker series colors. Read from tokens.css's semantic custom properties
  // where possible (falling back to their light-mode values — jsdom in
  // tests never resolves custom properties via getComputedStyle, and this
  // component doesn't attempt dark-mode-aware canvas colors, only DOM/CSS
  // ones get that via the cascade). Canvas fillStyle needs a literal
  // color, not a `var(...)` reference, hence resolving it up front here
  // rather than passing a CSS variable string into uPlot's series config.
  function cssVar(name: string, fallback: string): string {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return value || fallback
  }
  // Converts a `#rgb`/`#rrggbb` hex color to an `rgba(...)` string so an
  // alpha channel can be applied to it — canvas fillStyle needs a literal
  // RGB(A) triplet, not a bare hex string with alpha bolted on. tokens.css's
  // `--color-text-muted` is always hex in both its light and dark values,
  // so that's the only shape this needs to handle; anything else (e.g. a
  // future non-hex token value) is returned unchanged rather than mangled.
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
  const ARRHYTHMIA_BEAT_COLOR = cssVar('--color-danger', '#b91c1c')
  const NORMAL_BEAT_COLOR = cssVar('--color-accent', '#2563eb')
  // "Not yet evaluated" (any_arrhythmia: null) gets a dimmed treatment:
  // whatever `--color-text-muted` actually resolves to for the active
  // theme (light or dark), at reduced opacity — resolved the same
  // `cssVar`-at-mount way as the other two marker colors above, rather than
  // hardcoding the light-mode hex, so this doesn't render wrong from the
  // very first paint on a machine already in dark mode.
  const UNEVALUATED_BEAT_COLOR = hexToRgba(cssVar('--color-text-muted', '#5b6b7c'), 0.45)

  function markerSeriesConfig(color: string): uPlot.Series {
    return {
      paths: () => null,
      points: { show: true, size: 8, width: 1, stroke: color, fill: color },
    }
  }

  let containerEl: HTMLDivElement | undefined = $state()
  // Tracked via `bind:clientWidth` below. Read directly off `containerEl`
  // for the one-off initial-mount fetch instead (see onMount) so that fetch
  // isn't racing this binding's own first effect run.
  let containerWidth: number = $state(0)
  let error: string | null = $state(null)

  // Bad-data marking. `badDataMode` gates handleDragStart below: off (the
  // default) a drag pans the graph exactly as before; on, the same drag
  // instead selects a range to send to `addBadData`. Marks are session-local
  // state only — F3 doesn't load persisted marks (that's F5's `GET
  // /files/state` reopen flow) — rendered as a small proportional strip
  // beneath the graph (`markLeftPct`/`markWidthPct`, relative to the full
  // file extent) rather than as a canvas overlay synced to the current
  // pan/zoom window: it's simpler, and a technician can see/remove a mark
  // regardless of which part of the recording is currently in view.
  let badDataMode: boolean = $state(false)
  let badDataMarks: BadDataMark[] = $state([])

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

  async function refetch(min: number, max: number) {
    const result = await fetchMergedWindow(min, max, containerWidth)
    if (result && chart) {
      const { merged } = result
      chart.setData([merged.xs, merged.channelY, merged.arrhythmiaY, merged.normalY, merged.unevaluatedY], false)
    }
  }

  function scheduleRefetch(min: number, max: number) {
    if (debounceTimer !== undefined) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      debounceTimer = undefined
      void refetch(min, max)
    }, DEBOUNCE_MS)
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
      u.setScale('x', { min: scaleMin0 - dx, max: scaleMax0 - dx })
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

    u.setScale('x', { min: newMin, max: newMax })
    scheduleRefetch(newMin, newMax)
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

      chart = new uPlot(
        {
          width,
          height: HEIGHT,
          scales: { x: { time: false } },
          series: [
            {},
            { stroke: '#1a7f37', spanGaps: true },
            markerSeriesConfig(ARRHYTHMIA_BEAT_COLOR),
            markerSeriesConfig(NORMAL_BEAT_COLOR),
            markerSeriesConfig(UNEVALUATED_BEAT_COLOR),
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
    chart.setSize({ width, height: HEIGHT })
    const { min, max } = chart.scales.x
    if (min != null && max != null) scheduleRefetch(min, max)
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
    chart.setScale('x', { min: fullExtent.start, max: fullExtent.end })
    void refetch(fullExtent.start, fullExtent.end)
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

<div data-testid="ecg-graph" data-channel={channel}>
  <button data-testid="reset-view-button" onclick={handleResetView}>Reset view</button>
  <button
    type="button"
    data-testid="bad-data-mode-button"
    aria-pressed={badDataMode}
    onclick={toggleBadDataMode}
  >
    {badDataMode ? 'Exit bad data mode' : 'Mark bad data'}
  </button>

  {#if error}
    <div data-testid="ecg-graph-error">{error}</div>
  {/if}

  <div data-testid="ecg-graph-container" bind:this={containerEl} bind:clientWidth={containerWidth}></div>

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
