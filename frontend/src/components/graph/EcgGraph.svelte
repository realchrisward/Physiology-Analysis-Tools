<script module lang="ts">
  import type UplotNS from 'uplot'
  import type { WindowBeat } from '../../lib/api/types'
  import { MARKER_BUCKETS, primaryDisplayCategory } from '../../lib/categories'

  export interface BeatSeriesData {
    xs: number[]
    channelY: (number | null)[]
    // One y-array per `MARKER_BUCKETS` entry, keyed by that bucket's `key` —
    // this shape (a map, not fixed fields) is what lets the marker system
    // support 16 category×reviewed-state combinations without 16 named
    // fields; `toChartData` below flattens it into `MARKER_BUCKETS`' fixed
    // order to build uPlot's data array.
    markerY: Record<string, (number | null)[]>
    // The currently-selected beat's r_amplitude at its own x, null
    // elsewhere — a dedicated highlight-ring series, kept in this same
    // merged structure so it always shares the exact same `xs` as
    // everything else (see `applySelectionHighlight`).
    selectedY: (number | null)[]
    // Every rejected beat's r_amplitude at its own x, null elsewhere — a
    // dedicated overlay series (see `rejectedOverlaySeries`) drawn on top of
    // the 'normal' category marker a rejected beat already renders as, so
    // "rejected" stays visually distinct from "never flagged" even though
    // both are the same green circle underneath.
    rejectedY: (number | null)[]
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
   * Each beat is bucketed into exactly one of `MARKER_BUCKETS` (its
   * `primaryDisplayCategory` crossed with whether it's been reviewed), so it
   * renders as its own uPlot points-only series with a distinct
   * color/shape/fill — see `categoryMarkerSeries`. This function is the
   * exact "series/point data passed to uPlot" the marker tests assert
   * against, exported here so those tests can verify the merge/
   * categorization logic directly rather than reaching into a live uPlot
   * instance's internals.
   */
  export function buildBeatAlignedData(
    channelX: number[],
    channelY: number[],
    beats: WindowBeat[],
    selectedTs?: number | null,
    hideRejected: boolean = false,
  ): BeatSeriesData {
    const channelYByX = new Map<number, number>()
    channelX.forEach((x, i) => channelYByX.set(x, channelY[i]))

    // Hiding rejected beats has to happen HERE rather than by toggling a
    // series' visibility: a rejected beat renders in the same 'normal'
    // marker bucket as a beat that was never flagged, so the two are only
    // separable in the data, not at the series level.
    if (hideRejected) {
      beats = beats.filter((beat) => beat.review_state !== 'rejected')
    }

    const beatByTs = new Map<number, WindowBeat>()
    for (const beat of beats) beatByTs.set(beat.ts, beat)

    const xsSet = new Set<number>(channelX)
    for (const beat of beats) xsSet.add(beat.ts)
    const xs = Array.from(xsSet).sort((a, b) => a - b)

    const channelYOut: (number | null)[] = []
    const markerY: Record<string, (number | null)[]> = {}
    for (const bucket of MARKER_BUCKETS) markerY[bucket.key] = []
    const selectedY: (number | null)[] = []
    const rejectedY: (number | null)[] = []

    for (const x of xs) {
      channelYOut.push(channelYByX.has(x) ? (channelYByX.get(x) ?? null) : null)

      const beat = beatByTs.get(x)
      const category = beat ? primaryDisplayCategory(beat) : null
      const reviewed = beat ? beat.review_state !== 'unreviewed' : false

      // Markers are drawn at the height of the trace ACTUALLY ON SCREEN at
      // this timestamp, not at the beat's stored `r_amplitude`. That
      // amplitude is measured on the highpass-filtered signal used for
      // detection, so against the raw trace it can sit a long way from the
      // R-peak it is supposed to be pointing at (measured at up to 27% of
      // the chart height on a real recording with baseline drift). Using
      // the displayed y keeps every marker on its beat in both the raw and
      // filtered views. `r_amplitude` remains the fallback for a beat whose
      // timestamp has no sample in the downsampled trace.
      const markerHeight = beat ? (channelYByX.get(beat.ts) ?? beat.r_amplitude) : null

      for (const bucket of MARKER_BUCKETS) {
        const matches = beat !== undefined && category === bucket.category && reviewed === bucket.reviewed
        markerY[bucket.key].push(matches ? markerHeight : null)
      }

      selectedY.push(beat && selectedTs != null && beat.ts === selectedTs ? markerHeight : null)
      rejectedY.push(beat && beat.review_state === 'rejected' ? markerHeight : null)
    }

    return { xs, channelY: channelYOut, markerY, selectedY, rejectedY }
  }

  /** Flattens a merged `BeatSeriesData` into uPlot's `data` array shape, in the exact series order the chart is constructed with. */
  export function toChartData(merged: BeatSeriesData): UplotNS.AlignedData {
    return [
      merged.xs,
      merged.channelY,
      ...MARKER_BUCKETS.map((bucket) => merged.markerY[bucket.key]),
      merged.selectedY,
      merged.rejectedY,
    ]
  }
</script>

<script lang="ts">
  import { onMount } from 'svelte'
  import uPlot from 'uplot'
  import 'uplot/dist/uPlot.min.css'
  import { getChannelWindow, getBeatsWindow } from '../../lib/api/windowing'
  import { addBadData, deleteBadData } from '../../lib/api/persistence'
  import { themeState } from '../../lib/stores/theme.svelte'
  import { shouldIgnoreShortcut } from '../../lib/shortcuts'
  import Icon from '../shared/Icon.svelte'
  import type { BadDataMark } from '../../lib/api/types'
  import {
    CATEGORY_COLOR_VAR,
    DISPLAY_CATEGORIES,
    DISPLAY_CATEGORY_LABELS,
    DISPLAY_CATEGORY_SHAPE,
    type DisplayCategory,
    type MarkerShape,
  } from '../../lib/categories'

  let {
    path,
    channel,
    onBeatSelect,
    beatsRefreshToken = 0,
    initialBadDataMarks,
    selectedBeatTs = null,
    beatsOfInterest = [],
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
    // The `ts` of the beat currently selected in BeatCategoryPanel, if any —
    // drawn as a highlight ring (see `selectionHighlightSeries`) so it's
    // obvious at a glance which marker the detail panel refers to. A live
    // prop, not seeded-once: `applySelectionHighlight`'s effect reacts to it
    // changing for as long as this component stays mounted.
    selectedBeatTs?: number | null
    // Every flagged beat's `ts` across the WHOLE file, in time order (from
    // `GET /beats/of-interest` via ReviewWorkspace) — what the beat-of-
    // interest navigation steps through. Deliberately the whole file rather
    // than the visible window: the point of the navigation is to carry the
    // technician to flagged beats they can't currently see. A live prop:
    // arrhythmia detection usually resolves AFTER this component mounts, and
    // a review action can change which beats are flagged at any time.
    beatsOfInterest?: number[]
  } = $props()

  const DEBOUNCE_MS = 150
  const HEIGHT = 380
  const EXPANDED_HEIGHT = 640
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
  // Width of the view when jumping to a beat of interest. The whole point of
  // the jump is to land on a focused stretch of trace where the beat's SHAPE
  // is readable — at a mouse's ~600bpm this is roughly 20 beats of context,
  // enough to judge a beat against its neighbours without hunting for it.
  const FOCUS_WINDOW_SECONDS = 2
  // A page-forward/back step moves by slightly less than a full screen so a
  // sliver of the previous view stays visible as a visual anchor, the same
  // way a document reader's page-down does.
  const PAGE_OVERLAP_RATIO = 0.9
  const TOOLS_POS_KEY = 'pat.graphToolsPos'

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
  const axisColor = () => cssVar('--color-text-muted', '#5b6b7c')
  const gridColor = () => cssVar('--color-graph-grid', 'rgba(211, 218, 225, 0.6)')

  // Fallback hex per display category, used only when the corresponding
  // CSS custom property can't be resolved (e.g. jsdom in tests, which never
  // resolves custom properties via getComputedStyle) — same role
  // `waveformColor`'s own fallback already plays.
  const CATEGORY_FALLBACK_COLOR: Record<DisplayCategory, string> = {
    normal: '#15803d',
    unevaluated: '#5b6b7c',
    bradycardia_absolute: '#2563eb',
    tachycardia_absolute: '#dc2626',
    skipped_beat: '#7c3aed',
    prem_beat: '#d97706',
    abn_cluster: '#db2777',
    other_arrhythmia: '#0d9488',
  }

  // "Not yet evaluated" beats stay a dimmed treatment (as before this
  // category system existed); "normal" reuses the app's existing success
  // token rather than adding a 7th arrhythmia-style token for a
  // non-arrhythmia state; every real arrhythmia category resolves its own
  // token from lib/categories.ts's CATEGORY_COLOR_VAR.
  // The CSS custom property name backing a display category's color — used
  // both to resolve the actual color (below) and, as a var() reference
  // string, to color the legend's swatch icons directly in CSS.
  function legendColorVarName(cat: DisplayCategory): string {
    if (cat === 'normal') return '--color-success'
    if (cat === 'unevaluated') return '--color-text-muted'
    return CATEGORY_COLOR_VAR[cat]
  }

  function displayCategoryColor(cat: DisplayCategory): string {
    if (cat === 'unevaluated') {
      return hexToRgba(cssVar(legendColorVarName(cat), CATEGORY_FALLBACK_COLOR.unevaluated), 0.55)
    }
    return cssVar(legendColorVarName(cat), CATEGORY_FALLBACK_COLOR[cat])
  }

  // Draws one marker shape centered at (cx, cy) with "radius" r into an
  // existing Path2D — shape is the colorblind/black-&-white-theme-safe way
  // two categories stay distinguishable even when their colors alone
  // wouldn't be (see lib/categories.ts's CATEGORY_SHAPE comment).
  function addShapeToPath(path: Path2D, shape: MarkerShape, cx: number, cy: number, r: number): void {
    switch (shape) {
      case 'circle':
        path.moveTo(cx + r, cy)
        path.arc(cx, cy, r, 0, 2 * Math.PI)
        break
      case 'square': {
        const s = r * 1.6
        path.rect(cx - s / 2, cy - s / 2, s, s)
        break
      }
      case 'triangle': {
        const h = r * 1.8
        path.moveTo(cx, cy - h / 1.6)
        path.lineTo(cx - h / 1.6, cy + h / 2.2)
        path.lineTo(cx + h / 1.6, cy + h / 2.2)
        path.closePath()
        break
      }
      case 'diamond': {
        const s = r * 1.3
        path.moveTo(cx, cy - s)
        path.lineTo(cx + s, cy)
        path.lineTo(cx, cy + s)
        path.lineTo(cx - s, cy)
        path.closePath()
        break
      }
      case 'cross': {
        const s = r * 0.85
        const w = Math.max(1, r * 0.35)
        path.moveTo(cx - s, cy - s + w)
        path.lineTo(cx - s + w, cy - s)
        path.lineTo(cx, cy - w)
        path.lineTo(cx + s - w, cy - s)
        path.lineTo(cx + s, cy - s + w)
        path.lineTo(cx + w, cy)
        path.lineTo(cx + s, cy + s - w)
        path.lineTo(cx + s - w, cy + s)
        path.lineTo(cx, cy + w)
        path.lineTo(cx - s + w, cy + s)
        path.lineTo(cx - s, cy + s - w)
        path.lineTo(cx - w, cy)
        path.closePath()
        break
      }
      case 'plus': {
        const s = r
        const w = Math.max(1, r * 0.4)
        path.rect(cx - w / 2, cy - s, w, s * 2)
        path.rect(cx - s, cy - w / 2, s * 2, w)
        break
      }
    }
  }

  // A uPlot `points.paths` implementation: iterates this series' own data
  // in [idx0, idx1], converts each point to pixel space via `u.valToPos`,
  // and draws `shape` at each — returning both `stroke` and `fill` (when
  // `filled`) as the SAME Path2D, which is how uPlot fills+strokes custom
  // point shapes. `filled: false` omits the fill path entirely, leaving a
  // hollow outline-only marker (used for not-yet-reviewed beats — see
  // `categoryMarkerSeries`).
  function makeShapePathsFn(
    shape: MarkerShape,
    pxSize: number,
    filled: boolean,
  ): NonNullable<uPlot.Series['points']>['paths'] {
    return (u: uPlot, seriesIdx: number, idx0: number, idx1: number) => {
      const path = new Path2D()
      const series = u.series[seriesIdx]
      const scaleKey = series.scale ?? 'y'
      const yData = u.data[seriesIdx] as (number | null)[]
      const xData = u.data[0] as number[]
      const r = pxSize / 2
      for (let i = idx0; i <= idx1; i++) {
        const xVal = xData[i]
        const yVal = yData[i]
        if (xVal == null || yVal == null) continue
        const cx = u.valToPos(xVal, 'x', true)
        const cy = u.valToPos(yVal, scaleKey, true)
        addShapeToPath(path, shape, cx, cy, r)
      }
      return filled ? { stroke: path, fill: path } : { stroke: path }
    }
  }

  // One uPlot series per (category, reviewed) bucket — see MARKER_BUCKETS.
  // Reviewed beats render larger, filled, and with a heavier outline than
  // unreviewed ones (which are smaller and hollow), on top of the
  // category's own distinct color+shape — the "already-annotated vs not"
  // distinction requested alongside category coloring.
  function categoryMarkerSeries(category: DisplayCategory, reviewed: boolean): uPlot.Series {
    const shape = DISPLAY_CATEGORY_SHAPE[category]
    const colorFn = () => displayCategoryColor(category)
    const size = reviewed ? 9 : 6
    return {
      points: {
        show: true,
        size,
        width: reviewed ? 2 : 1.25,
        stroke: colorFn,
        fill: colorFn,
        paths: makeShapePathsFn(shape, size, reviewed),
      },
    }
  }

  // The currently-selected beat's highlight ring — always a hollow circle
  // (not the category's own shape, so it reads as "a ring around the real
  // marker" regardless of what that marker looks like) in the accent color.
  function selectionHighlightSeries(): uPlot.Series {
    const size = 18
    return {
      points: {
        show: true,
        size,
        width: 2.5,
        stroke: () => cssVar('--color-accent', '#2563eb'),
        paths: makeShapePathsFn('circle', size, false),
      },
    }
  }

  // Rejecting a beat clears every category flag (it genuinely wasn't an
  // arrhythmia), so it correctly buckets as 'normal' just like a beat that
  // was never flagged in the first place — but that collapses "a human
  // reviewed and dismissed this" into the same marker as "this was never
  // suspicious." This overlay draws a small diagonal strike on top of any
  // rejected beat's own marker so the two stay distinguishable at a glance,
  // without needing a whole new category/color for what is still,
  // correctly, "not an arrhythmia."
  function rejectedOverlaySeries(): uPlot.Series {
    const size = 9 // matches the reviewed-marker size so the strike spans it
    return {
      points: {
        show: true,
        size,
        width: 2,
        stroke: () => cssVar('--color-danger', '#b91c1c'),
        paths: (u: uPlot, seriesIdx: number, idx0: number, idx1: number) => {
          const path = new Path2D()
          const yData = u.data[seriesIdx] as (number | null)[]
          const xData = u.data[0] as number[]
          const r = size / 2
          for (let i = idx0; i <= idx1; i++) {
            const xVal = xData[i]
            const yVal = yData[i]
            if (xVal == null || yVal == null) continue
            const cx = u.valToPos(xVal, 'x', true)
            const cy = u.valToPos(yVal, 'y', true)
            path.moveTo(cx - r, cy - r)
            path.lineTo(cx + r, cy + r)
          }
          return { stroke: path }
        },
      },
    }
  }

  // Focus mode: the graph takes over the whole window and the toolbars
  // collapse into a floating panel in the corner, so a long review pass has
  // the maximum possible trace on screen without losing the controls.
  let fullscreen: boolean = $state(false)
  // Lets that floating panel get out of the way entirely when the
  // technician just wants to look at the trace.
  let toolsCollapsed: boolean = $state(false)
  // Tracked so the chart can be sized to the window while in focus mode.
  let viewportHeight: number = $state(typeof window === 'undefined' ? 800 : window.innerHeight)

  function graphHeight(): number {
    // Leaves room for the legend and marks strip beneath the plot.
    if (fullscreen) return Math.max(240, viewportHeight - 210)
    return expanded ? EXPANDED_HEIGHT : HEIGHT
  }

  function toggleFullscreen() {
    fullscreen = !fullscreen
    if (!fullscreen) toolsCollapsed = false
  }

  function toggleTools() {
    toolsCollapsed = !toolsCollapsed
  }

  // Where the floating tools panel sits in focus mode. `null` means "the
  // default top-right corner"; once the technician drags it, an explicit
  // position takes over and is remembered across sessions, since where the
  // panel should live depends on which part of the trace they're working on.
  let toolsPos: { x: number; y: number } | null = $state(loadToolsPos())
  let toolsEl: HTMLDivElement | undefined = $state()
  let draggingTools: boolean = $state(false)

  function loadToolsPos(): { x: number; y: number } | null {
    try {
      const raw = localStorage.getItem(TOOLS_POS_KEY)
      if (!raw) return null
      const parsed = JSON.parse(raw)
      if (typeof parsed?.x === 'number' && typeof parsed?.y === 'number') return parsed
    } catch {
      // Unreadable/absent storage just means "use the default corner".
    }
    return null
  }

  /** Keeps the panel on screen, including after a window resize. */
  function clampToolsPos(x: number, y: number): { x: number; y: number } {
    const width = toolsEl?.offsetWidth ?? 320
    const height = toolsEl?.offsetHeight ?? 120
    // Always leave a grabbable sliver visible, even if dragged to an edge.
    const maxX = Math.max(0, window.innerWidth - Math.min(width, 120))
    const maxY = Math.max(0, window.innerHeight - Math.min(height, 60))
    return { x: Math.max(0, Math.min(maxX, x)), y: Math.max(0, Math.min(maxY, y)) }
  }

  function startToolsDrag(event: MouseEvent) {
    if (!fullscreen) return
    event.preventDefault()

    const rect = toolsEl?.getBoundingClientRect()
    const offsetX = rect ? event.clientX - rect.left : 0
    const offsetY = rect ? event.clientY - rect.top : 0
    draggingTools = true

    function onMove(moveEvent: MouseEvent) {
      toolsPos = clampToolsPos(moveEvent.clientX - offsetX, moveEvent.clientY - offsetY)
    }
    function onUp() {
      draggingTools = false
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      endToolsDrag = undefined
      try {
        if (toolsPos) localStorage.setItem(TOOLS_POS_KEY, JSON.stringify(toolsPos))
      } catch {
        // Not being able to remember the position is not worth surfacing.
      }
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    // Same teardown discipline as the plot's own drags: a drag must never
    // outlive the component that started it.
    endToolsDrag = () => {
      draggingTools = false
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      endToolsDrag = undefined
    }
  }

  function resetToolsPos() {
    toolsPos = null
    try {
      localStorage.removeItem(TOOLS_POS_KEY)
    } catch {
      // ignore
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

  // Legend/filter state — see the legend template below and
  // `applyFilters`. Categories in this set are hidden (`chart.setSeries`);
  // empty means "show every category". `reviewFilter` narrows further by
  // reviewed/unreviewed, independent of which categories are shown.
  let hiddenCategories: Set<DisplayCategory> = $state(new Set())
  let reviewFilter: 'all' | 'reviewed' | 'unreviewed' = $state('all')
  // Hides beats the technician explicitly rejected, so a long review pass
  // isn't cluttered by decisions already made. Unlike the category/review
  // filters (pure series-visibility toggles), this one has to re-merge the
  // data: a rejected beat shares the 'normal' marker bucket with beats that
  // were never flagged, so it can't be hidden by `chart.setSeries` alone.
  let hideRejected: boolean = $state(false)
  // Shows the highpass-filtered trace (the signal beat detection actually
  // runs against) instead of the raw voltage — the old PySide6 app's
  // "plot filtered" checkbox.
  let showFiltered: boolean = $state(false)

  // Position within `beatsOfInterest`, or -1 before the technician has
  // jumped anywhere. Tracked here rather than derived from `selectedBeatTs`
  // so that clicking a beat directly on the graph doesn't scramble the
  // navigation's place in the list.
  let beatOfInterestIndex: number = $state(-1)
  // Guards the one-time auto-focus on the first beat of interest, which has
  // to wait for arrhythmia detection to resolve (usually after mount).
  let hasAutoFocused = false
  // `chart` itself is a plain (non-reactive) binding, so an effect that only
  // read it would never re-run once the async mount finally created it.
  // This is the reactive "the chart exists now" signal the auto-focus effect
  // needs, since the beats of interest it waits for can arrive either before
  // or after chart construction.
  let chartReady: boolean = $state(false)

  let containerEl: HTMLDivElement | undefined = $state()
  // Tracked via `bind:clientWidth` below. Read directly off `containerEl`
  // for the one-off initial-mount fetch instead (see onMount) so that fetch
  // isn't racing this binding's own first effect run.
  let containerWidth: number = $state(0)
  let error: string | null = $state(null)

  // Bad-data marking. `badDataMode` gates handleDragStart below: off (the
  // default) a drag pans the graph exactly as before; on, the same drag
  // instead selects a range to send to `addBadData`, and a plain click
  // removes whichever mark it lands on.
  //
  // Marks are drawn onto the chart canvas itself (see `badDataPlugin`), so
  // they stay aligned with the trace at any pan/zoom. The overview strip
  // beneath the graph is kept as a whole-file minimap — useful for seeing
  // marks that are currently off-screen — but is no longer the only place
  // they appear.
  let badDataMode: boolean = $state(false)
  // Seeded from `initialBadDataMarks` (F5 reopen hydration) instead of
  // always starting empty — see that prop's own comment above for why this
  // is a one-time read, not an ongoing sync.
  let badDataMarks: BadDataMark[] = $state(initialBadDataMarks ?? [])
  // The range currently being dragged out, painted live so the technician
  // sees what they're about to mark. `null` when no drag is in progress.
  let dragSelection: { start: number; stop: number } | null = $state(null)
  // The mark under the cursor in bad-data mode, highlighted to show that
  // clicking will remove it.
  let hoveredMarkId: number | null = $state(null)
  // Transient confirmation of the last add/remove, so an action that
  // otherwise only changes some shading has visible feedback.
  let badDataToast: string | null = $state(null)
  let badDataToastTimer: ReturnType<typeof setTimeout> | undefined

  function showBadDataToast(message: string) {
    badDataToast = message
    if (badDataToastTimer !== undefined) clearTimeout(badDataToastTimer)
    badDataToastTimer = setTimeout(() => {
      badDataToast = null
      badDataToastTimer = undefined
    }, 2400)
  }

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
  // Teardown for an in-progress tools-panel drag, same discipline as above.
  let endToolsDrag: (() => void) | undefined
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
  // Same skip-the-initial-run discipline for the raw/filtered toggle.
  let previousShowFiltered: boolean | undefined

  // The x-range actually LOADED into the chart right now (normally wider
  // than what's visible — see PAD_FACTOR below), and the visible-range
  // width that was requested when it was fetched (used to detect "zoomed
  // in enough since then that this buffer's point density is now too
  // coarse"). `undefined` until the first fetch (mount) completes.
  let loadedRange: { start: number; end: number; requestedWidth: number } | undefined
  // Guards `maybeBufferAhead` against firing overlapping fetches for
  // (approximately) the same range on consecutive mousemove ticks during a
  // single fast drag — a request-frequency throttle, independent of
  // `dataRequestId` below (which guards response *correctness*, not
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
  // Bumped by EVERY operation that will end in a `chart.setData` — a
  // pan/zoom/reset refetch, a beats-only refresh, or a jump. Each captures
  // the value and only applies its result if it is still the newest, so a
  // slow response can never overwrite a newer one's data.
  let dataRequestId = 0

  function resolutionFor(width: number): number {
    return Math.max(1, Math.round(width))
  }

  async function loadWindow(start: number, end: number, width: number): Promise<{ x: number[]; y: number[] } | null> {
    const result = await getChannelWindow(path, channel, start, end, resolutionFor(width), showFiltered)
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
  // Deliberately does NOT write `lastBeats` itself. A response that lost a
  // race must not become the hit-testing source for clicks, so the caller
  // assigns it only after confirming its own request is still the current
  // one (see `dataRequestId`).
  async function loadBeats(start: number, end: number): Promise<WindowBeat[]> {
    const result = await getBeatsWindow(path, start, end)
    if (result.status !== 'ok' || result.error || !Array.isArray(result.beats)) {
      return []
    }
    return result.beats
  }

  async function fetchMergedWindow(
    start: number,
    end: number,
    width: number,
  ): Promise<{
    channel: { x: number[]; y: number[] }
    beats: WindowBeat[]
    merged: BeatSeriesData
  } | null> {
    const channelData = await loadWindow(start, end, width)
    if (!channelData) return null
    const beats = await loadBeats(start, end)
    // `lastChannelWindow`/`lastBeats` are assigned by the caller, after its
    // staleness check — committing them here would let a superseded fetch
    // leave the retained state describing a window that is no longer shown.
    return {
      channel: channelData,
      beats,
      merged: buildBeatAlignedData(channelData.x, channelData.y, beats, selectedBeatTs, hideRejected),
    }
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

    // Shares ONE counter with `refetch` rather than keeping its own: these
    // two paths both call setData, and a beats-only refresh merges against
    // `lastChannelWindow`, which a concurrent refetch is about to replace.
    // Without a shared guard, a slow refresh landing after a pan would
    // paint beats from the old range over the new range's waveform.
    const requestId = ++dataRequestId
    const beats = await loadBeats(min, max)
    if (requestId !== dataRequestId || !chart || !lastChannelWindow) return

    lastBeats = beats
    const merged = buildBeatAlignedData(lastChannelWindow.x, lastChannelWindow.y, beats, selectedBeatTs, hideRejected)
    chart.setData(toChartData(merged), false)
    applyFilters()
  }

  // Re-applies just the selection-highlight ring against whatever waveform
  // window is currently on screen, without any network round trip — beats
  // are cheap to have retained (`lastBeats`) and re-merging is pure local
  // computation. Guarded on `chart`/`lastChannelWindow` existing, same as
  // `refreshBeatsOnly` above, so it's a safe no-op before the first mount
  // fetch has resolved.
  function applySelectionHighlight() {
    if (!chart || !lastChannelWindow) return
    const merged = buildBeatAlignedData(lastChannelWindow.x, lastChannelWindow.y, lastBeats, selectedBeatTs, hideRejected)
    chart.setData(toChartData(merged), false)
    applyFilters()
  }

  // Shows/hides marker series per the legend's category toggles and the
  // reviewed/unreviewed filter — `chart.setSeries` alone, no data
  // re-merge/re-fetch needed; the underlying data for a hidden category is
  // still there, just not drawn.
  function applyFilters() {
    if (!chart) return

    function bucketVisible(category: DisplayCategory, reviewed: boolean): boolean {
      const categoryVisible = !hiddenCategories.has(category)
      const reviewVisible =
        reviewFilter === 'all' || (reviewFilter === 'reviewed' ? reviewed : !reviewed)
      return categoryVisible && reviewVisible
    }

    MARKER_BUCKETS.forEach((bucket, i) => {
      const seriesIdx = i + 2 // series[0]=x, series[1]=waveform, then MARKER_BUCKETS in order
      chart!.setSeries(seriesIdx, { show: bucketVisible(bucket.category, bucket.reviewed) })
    })

    // The rejected-beat strike is an overlay ON the 'normal' reviewed
    // marker, so it has to follow that marker's visibility. Left unfiltered
    // it painted strikes over markers that were no longer drawn — floating
    // marks with nothing underneath them.
    const rejectedSeriesIdx = MARKER_BUCKETS.length + 3
    chart.setSeries(rejectedSeriesIdx, { show: bucketVisible('normal', true) })
  }

  function toggleCategoryFilter(cat: DisplayCategory): void {
    const next = new Set(hiddenCategories)
    if (next.has(cat)) next.delete(cat)
    else next.add(cat)
    hiddenCategories = next
  }

  function setReviewFilter(value: 'all' | 'reviewed' | 'unreviewed'): void {
    reviewFilter = value
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
  async function refetch(
    min: number,
    max: number,
    options: { pad?: boolean; resetScales?: boolean } = {},
  ) {
    const requestId = ++dataRequestId
    const usePad = options.pad ?? true
    const requestedWidth = max - min
    const pad = usePad ? requestedWidth * PAD_FACTOR : 0
    const fetchStart = min - pad
    const fetchEnd = max + pad
    const fetchWidth = usePad ? containerWidth * (1 + 2 * PAD_FACTOR) : containerWidth

    const result = await fetchMergedWindow(fetchStart, fetchEnd, fetchWidth)
    // Superseded by a newer refetch (a later pan/zoom, or a Reset) dispatched
    // while this one was in flight — see `dataRequestId`'s own comment
    // above for why applying a stale response here would be a real bug
    // (the "reset zooms out too much until a second click" report).
    if (requestId !== dataRequestId) return

    if (result && chart) {
      const { channel: channelData, beats, merged } = result
      // Commit the retained window/beats only now that this response is
      // confirmed current — these drive click hit-testing and every later
      // local re-merge, so stale values here cause silently wrong behaviour
      // rather than a visible glitch.
      lastChannelWindow = channelData
      lastBeats = beats
      // `resetScales: true` (Reset View only) hands BOTH x and y back to
      // uPlot's own auto-ranging from this fresh data — the only supported
      // way to undo a manual Y-zoom's `setScale` pin; uPlot normalizes
      // `Scale.auto` into an internal function at construction time, so
      // directly reassigning `chart.scales.y.auto = true` afterward corrupts
      // it (throws `sc.auto is not a function` on the next commit) rather
      // than re-enabling auto-ranging. X is re-pinned to the exact requested
      // range right after regardless, since resetScales' own x auto-range
      // depends on this fetch's returned data matching `[min, max]` exactly,
      // which is true for Reset View's own unpadded call but is never
      // assumed here.
      chart.setData(toChartData(merged), options.resetScales ?? false)
      if (options.resetScales) {
        chart.setScale('x', { min, max })
      }
      loadedRange = { start: fetchStart, end: fetchEnd, requestedWidth }
      applyFilters() // setData resets series visibility; re-apply the active category/review filters
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
      const span = Math.abs(stop - start)
      showBadDataToast(`Marked ${span.toFixed(2)}s as bad data`)
    } else {
      error = result.error ?? 'Failed to add bad data mark'
    }
  }

  async function removeBadDataMark(mark: BadDataMark) {
    const result = await deleteBadData(path, mark.id)
    if (result.status === 'ok' && !result.error) {
      badDataMarks = badDataMarks.filter((m) => m.id !== mark.id)
      if (hoveredMarkId === mark.id) hoveredMarkId = null
      showBadDataToast('Bad-data mark removed')
    } else {
      error = result.error ?? 'Failed to remove bad data mark'
    }
  }

  function toggleBadDataMode() {
    badDataMode = !badDataMode
    if (!badDataMode) hoveredMarkId = null
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
    const startPx = downEvent.clientX

    function detach() {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      dragSelection = null
      endActiveDrag = undefined
    }

    // Live feedback: the selection is painted on every move (see
    // `badDataPlugin`), so the technician can see the exact range they are
    // about to mark rather than finding out only after releasing.
    function onMove(moveEvent: MouseEvent) {
      dragSelection = { start: startVal, stop: u.posToVal(moveEvent.clientX - rect.left, 'x') }
      chart?.redraw()
    }

    function onUp(upEvent: MouseEvent) {
      const movedPx = Math.abs(upEvent.clientX - startPx)
      detach()
      chart?.redraw()

      // A click rather than a drag: in this mode that means "remove the mark
      // I clicked on", which is the natural inverse of drawing one, instead
      // of silently creating a zero-width mark.
      if (movedPx < CLICK_DRAG_THRESHOLD_PX) {
        const existing = markAt(u.posToVal(upEvent.clientX - rect.left, 'x'))
        if (existing) void removeBadDataMark(existing)
        return
      }

      void submitBadDataMark(startVal, u.posToVal(upEvent.clientX - rect.left, 'x'))
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    endActiveDrag = detach
  }

  // Highlights whichever mark the cursor is over while in bad-data mode, so
  // it's obvious that clicking will remove that one.
  function handleBadDataHover(u: uPlot, event: MouseEvent) {
    if (!badDataMode || dragSelection) return
    const rect = u.over.getBoundingClientRect()
    const hovered = markAt(u.posToVal(event.clientX - rect.left, 'x'))
    const next = hovered?.id ?? null
    if (next !== hoveredMarkId) {
      hoveredMarkId = next
      chart?.redraw()
    }
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
  // Plain wheel/scroll zooms X (unchanged); Shift+wheel zooms Y instead —
  // the common cross-application convention for a secondary-axis zoom, so
  // it needs no new persistent UI to discover (the Y zoom buttons in the
  // toolbar are the discoverable path for technicians who don't know the
  // modifier). Y is purely a rendering concern — the already-loaded
  // waveform's amplitude values don't change, only how much vertical space
  // they're drawn into — so it never touches `scheduleRefetch`/
  // `maybeBufferAhead` at all, unlike every X-axis zoom path.
  function handleWheel(u: uPlot, wheelEvent: WheelEvent) {
    wheelEvent.preventDefault()

    if (wheelEvent.shiftKey) {
      const oldMin = u.scales.y.min ?? 0
      const oldMax = u.scales.y.max ?? 0
      const oldRange = oldMax - oldMin
      const newRange = wheelEvent.deltaY < 0 ? oldRange * ZOOM_FACTOR : oldRange / ZOOM_FACTOR
      const mid = (oldMin + oldMax) / 2
      viewChanged = true
      u.setScale('y', { min: mid - newRange / 2, max: mid + newRange / 2 })
      return
    }

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

  // Y-axis equivalent of `zoomBy` — no fetch needed at all (see
  // `handleWheel`'s own comment on why Y-zoom is a pure rendering concern).
  function zoomYBy(factor: number) {
    if (!chart) return
    const { min, max } = chart.scales.y
    if (min == null || max == null) return
    const mid = (min + max) / 2
    const newRange = (max - min) * factor
    viewChanged = true
    chart.setScale('y', { min: mid - newRange / 2, max: mid + newRange / 2 })
  }

  function handleZoomIn() {
    zoomBy(ZOOM_FACTOR)
  }

  function handleZoomOut() {
    zoomBy(1 / ZOOM_FACTOR)
  }

  function handleZoomInY() {
    zoomYBy(ZOOM_FACTOR)
  }

  function handleZoomOutY() {
    zoomYBy(1 / ZOOM_FACTOR)
  }

  function toggleExpanded() {
    expanded = !expanded
  }

  // Moves the visible x-range to exactly [start, end] and loads the data for
  // it. Shared by every jump/page control below — unlike the pan/zoom paths
  // these are discrete, deliberate moves, so they fetch immediately rather
  // than through the 150ms debounce (which exists to coalesce continuous
  // gestures; there is nothing to coalesce here).
  function setVisibleRange(start: number, end: number): Promise<void> {
    if (!chart) return Promise.resolve()
    if (debounceTimer !== undefined) {
      clearTimeout(debounceTimer)
      debounceTimer = undefined
    }
    viewChanged = true
    chart.setScale('x', { min: start, max: end })
    return refetch(start, end)
  }

  // Centres the view on `ts`, keeping the current zoom level if the
  // technician has already zoomed in tighter than the default focus window.
  function focusOnTime(ts: number): Promise<void> {
    if (!chart) return Promise.resolve()
    const { min, max } = chart.scales.x
    const currentWidth = min != null && max != null ? max - min : FOCUS_WINDOW_SECONDS
    const width = Math.min(currentWidth, FOCUS_WINDOW_SECONDS)
    return setVisibleRange(ts - width / 2, ts + width / 2)
  }

  // Jumping to a beat of interest also SELECTS it, so the detail panel shows
  // that beat's categories and the confirm/reject controls act on it — the
  // review loop the old app's next/prev-arrhythmia buttons drove.
  async function goToBeatOfInterest(index: number) {
    if (beatsOfInterest.length === 0) return
    const clamped = Math.max(0, Math.min(beatsOfInterest.length - 1, index))
    beatOfInterestIndex = clamped
    const ts = beatsOfInterest[clamped]

    // Select immediately when the beat is already loaded, so the detail
    // panel responds without waiting on the network.
    const loaded = lastBeats.find((b) => b.ts === ts)
    if (loaded) onBeatSelect?.(loaded)

    const pending = focusOnTime(ts)

    // Jumping somewhere outside the loaded buffer — a long recording, or a
    // jump straight to the last flagged beat — means the beat isn't in
    // `lastBeats` yet. Without this retry the view moved but nothing was
    // selected, so the review controls silently acted on the wrong beat (or
    // on nothing at all).
    if (!loaded) {
      await pending
      const fetched = lastBeats.find((b) => b.ts === ts)
      // Still the beat the technician asked for? A newer jump may have
      // superseded this one while the fetch was in flight.
      if (fetched && beatsOfInterest[beatOfInterestIndex] === ts) onBeatSelect?.(fetched)
    }
  }

  function handleFirstBeatOfInterest() {
    void goToBeatOfInterest(0)
  }

  function handlePrevBeatOfInterest() {
    // From "nowhere yet", stepping back lands on the last one rather than
    // doing nothing.
    void goToBeatOfInterest(beatOfInterestIndex <= 0 ? beatsOfInterest.length - 1 : beatOfInterestIndex - 1)
  }

  function handleNextBeatOfInterest() {
    void goToBeatOfInterest(beatOfInterestIndex + 1 >= beatsOfInterest.length ? 0 : beatOfInterestIndex + 1)
  }

  function handleLastBeatOfInterest() {
    void goToBeatOfInterest(beatsOfInterest.length - 1)
  }

  // Time navigation across the file at the current zoom — the old app's
  // start-of-file / previous-window / next-window / end-of-file controls.
  function handleJumpToStart() {
    if (!chart || !fullExtent) return
    const { min, max } = chart.scales.x
    if (min == null || max == null) return
    const width = max - min
    setVisibleRange(fullExtent.start, fullExtent.start + width)
  }

  function handleJumpToEnd() {
    if (!chart || !fullExtent) return
    const { min, max } = chart.scales.x
    if (min == null || max == null) return
    const width = max - min
    setVisibleRange(fullExtent.end - width, fullExtent.end)
  }

  function pageBy(direction: 1 | -1) {
    if (!chart) return
    const { min, max } = chart.scales.x
    if (min == null || max == null) return
    const step = (max - min) * PAGE_OVERLAP_RATIO * direction
    setVisibleRange(min + step, max + step)
  }

  function handlePrevWindow() {
    pageBy(-1)
  }

  function handleNextWindow() {
    pageBy(1)
  }

  function toggleFiltered() {
    showFiltered = !showFiltered
  }

  function toggleHideRejected() {
    hideRejected = !hideRejected
  }

  function panZoomPlugin(): uPlot.Plugin {
    return {
      hooks: {
        ready: (u) => {
          u.over.addEventListener('mousedown', (e) => handleDragStart(u, e))
          u.over.addEventListener('wheel', (e) => handleWheel(u, e))
          u.over.addEventListener('mousemove', (e) => handleBadDataHover(u, e))
          u.over.addEventListener('mouseleave', () => {
            if (hoveredMarkId !== null) {
              hoveredMarkId = null
              chart?.redraw()
            }
          })
        },
      },
    }
  }

  // Paints one time range as a shaded band across the full plot height.
  function paintRegion(
    u: uPlot,
    start: number,
    stop: number,
    fill: string,
    stroke: string,
  ): void {
    const { ctx } = u
    const left = u.valToPos(Math.min(start, stop), 'x', true)
    const right = u.valToPos(Math.max(start, stop), 'x', true)
    const top = u.bbox.top
    const height = u.bbox.height
    // Always at least a hairline wide, so a very short mark stays visible.
    const width = Math.max(2, right - left)

    ctx.fillStyle = fill
    ctx.fillRect(left, top, width, height)
    ctx.strokeStyle = stroke
    ctx.lineWidth = 1
    ctx.strokeRect(left + 0.5, top + 0.5, width - 1, height - 1)
  }

  // Draws bad-data regions onto the chart canvas itself, rather than as a
  // separate strip below it. This is what makes them stay aligned with the
  // trace at any pan/zoom — the previous proportional-strip rendering was
  // positioned against the WHOLE file, so once zoomed in it no longer told
  // the technician anything about the range actually on screen.
  //
  // Painted in `drawClear` (after the canvas is cleared, before the series
  // are drawn) so the waveform and markers stay legible on top of the
  // shading rather than being covered by it.
  function badDataPlugin(): uPlot.Plugin {
    return {
      hooks: {
        drawClear: (u) => {
          const { ctx } = u
          // Shading is decoration: if the canvas context can't paint it for
          // any reason, that must not take the surrounding interaction
          // (drag-to-mark, click-to-remove) down with it.
          try {
          ctx.save()
          ctx.beginPath()
          ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height)
          ctx.clip()

          const markFill = cssVar('--color-bad-data-fill', 'rgba(185, 28, 28, 0.13)')
          const markStroke = cssVar('--color-bad-data-stroke', 'rgba(185, 28, 28, 0.5)')
          for (const mark of badDataMarks) {
            const isHovered = mark.id === hoveredMarkId
            paintRegion(
              u,
              mark.start,
              mark.stop,
              isHovered ? cssVar('--color-bad-data-fill-hover', 'rgba(185, 28, 28, 0.28)') : markFill,
              markStroke,
            )
          }

          // The range currently being dragged out, so the technician can see
          // exactly what they're about to mark before releasing.
          if (dragSelection) {
            paintRegion(
              u,
              dragSelection.start,
              dragSelection.stop,
              cssVar('--color-bad-data-fill-hover', 'rgba(185, 28, 28, 0.28)'),
              cssVar('--color-danger', '#b91c1c'),
            )
          }

          ctx.restore()
          } catch {
            // ignore — see above
          }
        },
      },
    }
  }

  /** The bad-data mark covering `xVal`, if any — for click-to-remove. */
  function markAt(xVal: number): BadDataMark | undefined {
    return badDataMarks.find((mark) => xVal >= mark.start && xVal <= mark.stop)
  }

  onMount(() => {
    let cancelled = false

    void (async () => {
      const width = containerEl?.clientWidth ?? 0
      const result = await fetchMergedWindow(0, Number.MAX_SAFE_INTEGER, width)
      if (cancelled || !result || !containerEl) return
      const { channel: data, beats, merged } = result
      // `fetchMergedWindow` deliberately leaves these to its caller (see its
      // own comment), so the mount path has to commit them too — they are
      // what click hit-testing and every later local re-merge read from.
      lastChannelWindow = data
      lastBeats = beats

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
          height: graphHeight(),
          // `y.auto: true` is uPlot's own default (implicit until now) —
          // stated explicitly since `handleResetView` toggles it back on
          // after a manual Y-zoom, and Y-zoom (`zoomYBy`/Shift+wheel) turns
          // it off implicitly the same way X's own manual `setScale` always
          // has.
          scales: { x: { time: false }, y: { auto: true } },
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
          // uPlot auto-generates a "Value: --" legend row with one entry
          // per series by default — harmless with the original 3 marker
          // series, but with 16 (one per category × reviewed state) plus
          // the waveform and selection-highlight series, that became a
          // dense, uninformative grid of placeholder values (we don't wire
          // up hover-tracking, so every entry always reads "--"). Our own
          // legend below the graph (color+shape swatches, doubling as the
          // category filter) already covers this, so uPlot's built-in one
          // is switched off entirely rather than left as visual noise.
          legend: { show: false },
          axes: [
            { stroke: () => axisColor(), grid: { stroke: () => gridColor(), width: 1 }, ticks: { stroke: () => axisColor() } },
            { stroke: () => axisColor(), grid: { stroke: () => gridColor(), width: 1 }, ticks: { stroke: () => axisColor() } },
          ],
          series: [
            {},
            { stroke: () => waveformColor(), width: 1.5, spanGaps: true },
            ...MARKER_BUCKETS.map((bucket) => categoryMarkerSeries(bucket.category, bucket.reviewed)),
            selectionHighlightSeries(),
            rejectedOverlaySeries(),
          ],
          plugins: [panZoomPlugin(), badDataPlugin()],
        },
        toChartData(merged),
        containerEl,
      )
      applyFilters()
      chartReady = true
    })()

    return () => {
      cancelled = true
      if (debounceTimer !== undefined) clearTimeout(debounceTimer)
      // A drag in progress attaches its mousemove/mouseup listeners to
      // `document`, not to uPlot's own root node — `chart.destroy()` below
      // only removes uPlot's own DOM/listeners, so an active drag must be
      // torn down separately or it outlives the component.
      endActiveDrag?.()
      endToolsDrag?.()
      chart?.destroy()
      chart = undefined
      chartReady = false
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
    chart.setSize({ width, height: graphHeight() })
    const { min, max } = chart.scales.x
    if (min != null && max != null) scheduleRefetch(min, max)
  })

  // Applies the maximize/minimize toggle to the live chart. Guarded on
  // `chart` existing (it may still be mid-construction on the very first
  // run, before `expanded` could plausibly have changed from its initial
  // `false`) rather than needing to coordinate with the async onMount setup.
  $effect(() => {
    // Re-read every input to graphHeight() so this re-runs for any of them.
    expanded
    fullscreen
    viewportHeight
    if (!chart) return
    chart.setSize({ width: containerWidth, height: graphHeight() })
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

  // Re-draws the selection-highlight ring whenever the parent's selected
  // beat changes — see `applySelectionHighlight`'s own comment. Runs once
  // (harmlessly, as a no-op — see that function's own `chart`/
  // `lastChannelWindow` guard) on mount too, before either exists yet.
  $effect(() => {
    selectedBeatTs
    applySelectionHighlight()
  })

  // Re-applies the legend's category/review-state filters whenever either
  // changes — see `applyFilters`.
  $effect(() => {
    hiddenCategories
    reviewFilter
    applyFilters()
  })

  // Escape is the universal "get me out of this mode" key: it leaves
  // bad-data marking first (the more modal of the two, since it changes
  // what a drag does), then focus mode. The resize listener keeps the
  // focus-mode chart sized to the window.
  onMount(() => {
    function handleKeydown(event: KeyboardEvent) {
      // Escape works even while typing — it's the universal "get me out" —
      // but a modal owns it while open, otherwise dismissing Settings would
      // also drop the technician out of focus mode behind it.
      if (event.key === 'Escape') {
        if (document.querySelector('[role="dialog"]')) return
        if (badDataMode) {
          badDataMode = false
          hoveredMarkId = null
        } else if (fullscreen) {
          fullscreen = false
          toolsCollapsed = false
        }
        return
      }

      if (shouldIgnoreShortcut(event)) return

      // Walking hundreds of flagged beats is the core review loop, so it
      // shouldn't require moving the mouse between the graph and the panel
      // for every single one.
      switch (event.key) {
        case 'n':
        case 'N':
        case 'ArrowRight':
          if (beatsOfInterest.length === 0) return
          event.preventDefault()
          handleNextBeatOfInterest()
          break
        case 'p':
        case 'P':
        case 'ArrowLeft':
          if (beatsOfInterest.length === 0) return
          event.preventDefault()
          handlePrevBeatOfInterest()
          break
        case 'f':
        case 'F':
          event.preventDefault()
          toggleFullscreen()
          break
        case 'b':
        case 'B':
          event.preventDefault()
          toggleBadDataMode()
          break
        default:
          break
      }
    }
    function handleResize() {
      viewportHeight = window.innerHeight
      if (toolsPos) toolsPos = clampToolsPos(toolsPos.x, toolsPos.y)
    }
    document.addEventListener('keydown', handleKeydown)
    window.addEventListener('resize', handleResize)
    return () => {
      document.removeEventListener('keydown', handleKeydown)
      window.removeEventListener('resize', handleResize)
      if (badDataToastTimer !== undefined) clearTimeout(badDataToastTimer)
    }
  })

  // The bad-data plugin reads `badDataMarks` at draw time, so adding or
  // removing one has to ask for a repaint explicitly.
  $effect(() => {
    badDataMarks
    chart?.redraw()
  })

  // Hiding/showing rejected beats changes the DATA (see
  // `buildBeatAlignedData`), not just series visibility, so it needs a
  // re-merge — but no network round trip, since the beats are already held.
  $effect(() => {
    hideRejected
    applySelectionHighlight()
  })

  // Switching between the raw and highpass-filtered trace DOES need a real
  // re-fetch (the filtering happens backend-side, against the whole
  // channel), at exactly the currently-visible range so the viewport doesn't
  // move under the technician. Same skip-the-initial-run shape as the
  // `containerWidth`/`beatsRefreshToken` effects above.
  $effect(() => {
    const filtered = showFiltered
    if (previousShowFiltered === undefined) {
      previousShowFiltered = filtered
      return
    }
    if (filtered === previousShowFiltered) return
    previousShowFiltered = filtered

    if (!chart) return
    const { min, max } = chart.scales.x
    if (min == null || max == null) return
    void refetch(min, max)
  })

  // Auto-focus the first beat of interest, once, as soon as one exists.
  // Deliberately effect-driven rather than done inside `onMount`: arrhythmia
  // detection normally resolves AFTER this component has mounted, so at
  // mount time there is usually nothing to focus on yet. `hasAutoFocused`
  // makes it strictly one-shot — re-running detection, or reviewing a beat
  // away, must never yank the technician's view back to the top of the file.
  $effect(() => {
    const flagged = beatsOfInterest
    const ready = chartReady
    if (hasAutoFocused || flagged.length === 0 || !ready) return
    hasAutoFocused = true
    void goToBeatOfInterest(0)
  })

  function handleResetView() {
    if (!chart || !fullExtent) return
    if (debounceTimer !== undefined) {
      clearTimeout(debounceTimer)
      debounceTimer = undefined
    }
    viewChanged = false
    chart.setScale('x', { min: fullExtent.start, max: fullExtent.end })
    // `resetScales: true` also hands Y back to uPlot's own auto-ranging,
    // undoing any manual Y-zoom — see `refetch`'s own comment on why that
    // option (not a direct `chart.scales.y.auto` mutation) is the correct
    // way to do this.
    void refetch(fullExtent.start, fullExtent.end, { pad: false, resetScales: true })
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

<div class="ecg-graph" class:fullscreen data-testid="ecg-graph" data-channel={channel}>
  <!-- In focus mode this whole block detaches into a floating panel over
       the trace, and can be collapsed to a single button so nothing covers
       the signal. -->
  <div
    class="graph-tools"
    class:floating={fullscreen}
    class:collapsed={fullscreen && toolsCollapsed}
    class:dragging={draggingTools}
    bind:this={toolsEl}
    style={fullscreen && toolsPos ? `left: ${toolsPos.x}px; top: ${toolsPos.y}px; right: auto;` : ''}
  >
    {#if fullscreen}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="tools-bar" data-testid="graph-tools-bar" onmousedown={startToolsDrag}>
        <span class="tools-grip" title="Drag to move these tools" aria-hidden="true">
          <Icon name="grip" size={14} />
        </span>
        <span class="tools-bar-label">Tools</span>
        {#if toolsPos}
          <button
            type="button"
            class="btn btn-sm"
            data-testid="reset-tools-position-button"
            title="Move back to the corner"
            onclick={resetToolsPos}
          >
            Reset
          </button>
        {/if}
        <button
          type="button"
          class="btn btn-sm"
          data-testid="toggle-graph-tools-button"
          aria-expanded={!toolsCollapsed}
          title={toolsCollapsed ? 'Show tools' : 'Hide tools'}
          onclick={toggleTools}
        >
          <Icon name={toolsCollapsed ? 'chevron-down' : 'x'} size={14} />
          {toolsCollapsed ? 'Tools' : 'Hide'}
        </button>
      </div>
    {/if}

    {#if !(fullscreen && toolsCollapsed)}
  <!-- Beat-of-interest navigation: the primary review loop. The graph opens
       focused on the first flagged beat rather than the whole file, and
       these controls walk the technician through the rest. -->
  <div class="beat-nav" data-testid="beat-of-interest-nav">
    <div class="beat-nav-controls">
      <button
        type="button"
        class="btn btn-sm"
        data-testid="first-beat-of-interest-button"
        title="First beat of interest"
        disabled={beatsOfInterest.length === 0}
        onclick={handleFirstBeatOfInterest}
      >
        <Icon name="skip-back" size={14} />
      </button>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="prev-beat-of-interest-button"
        title="Previous beat of interest (P or Left arrow)"
        disabled={beatsOfInterest.length === 0}
        onclick={handlePrevBeatOfInterest}
      >
        <Icon name="chevron-left" size={14} />
      </button>
      <span class="beat-nav-counter" data-testid="beat-of-interest-counter">
        {#if beatsOfInterest.length === 0}
          No beats of interest
        {:else if beatOfInterestIndex < 0}
          {beatsOfInterest.length} beats of interest
        {:else}
          Beat of interest {beatOfInterestIndex + 1} of {beatsOfInterest.length}
        {/if}
      </span>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="next-beat-of-interest-button"
        title="Next beat of interest (N or Right arrow)"
        disabled={beatsOfInterest.length === 0}
        onclick={handleNextBeatOfInterest}
      >
        <Icon name="chevron-right" size={14} />
      </button>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="last-beat-of-interest-button"
        title="Last beat of interest"
        disabled={beatsOfInterest.length === 0}
        onclick={handleLastBeatOfInterest}
      >
        <Icon name="skip-forward" size={14} />
      </button>
      {#if beatsOfInterest.length > 0}
        <span class="nav-hint" data-testid="beat-nav-hint">
          <kbd>N</kbd>/<kbd>P</kbd> to step · <kbd>C</kbd>/<kbd>R</kbd> to judge
        </span>
      {/if}
    </div>

    <div class="beat-nav-controls">
      <button
        type="button"
        class="btn btn-sm"
        data-testid="jump-to-start-button"
        title="Start of recording"
        onclick={handleJumpToStart}
      >
        <Icon name="skip-back" size={14} /> Start
      </button>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="prev-window-button"
        title="Previous window"
        onclick={handlePrevWindow}
      >
        <Icon name="chevron-left" size={14} />
      </button>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="next-window-button"
        title="Next window"
        onclick={handleNextWindow}
      >
        <Icon name="chevron-right" size={14} />
      </button>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="jump-to-end-button"
        title="End of recording"
        onclick={handleJumpToEnd}
      >
        End <Icon name="skip-forward" size={14} />
      </button>
    </div>
  </div>

  <div class="ecg-graph-toolbar">
    <button
      type="button"
      class="btn btn-sm"
      class:btn-active={showFiltered}
      data-testid="toggle-filtered-signal-button"
      aria-pressed={showFiltered}
      title="Show the highpass-filtered trace that beat detection runs against"
      onclick={toggleFiltered}
    >
      <Icon name="filter" size={14} />
      {showFiltered ? 'Filtered' : 'Raw'}
    </button>
    <div class="zoom-group">
      <span class="zoom-group-label">X</span>
      <button type="button" class="btn btn-sm" data-testid="zoom-in-button" title="Zoom in (X)" onclick={handleZoomIn}>
        <Icon name="zoom-in" size={14} />
      </button>
      <button type="button" class="btn btn-sm" data-testid="zoom-out-button" title="Zoom out (X)" onclick={handleZoomOut}>
        <Icon name="zoom-out" size={14} />
      </button>
    </div>
    <div class="zoom-group">
      <span class="zoom-group-label">Y</span>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="zoom-in-y-button"
        title="Zoom in (Y) — or hold Shift and scroll"
        onclick={handleZoomInY}
      >
        <Icon name="zoom-in" size={14} />
      </button>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="zoom-out-y-button"
        title="Zoom out (Y) — or hold Shift and scroll"
        onclick={handleZoomOutY}
      >
        <Icon name="zoom-out" size={14} />
      </button>
    </div>
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
      title={badDataMode ? 'Leave bad-data marking (Esc)' : 'Mark unusable stretches of trace (B)'}
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
    <button
      type="button"
      class="btn btn-sm"
      class:btn-active={fullscreen}
      data-testid="fullscreen-graph-button"
      title={fullscreen ? 'Leave focus mode (Esc)' : 'Focus mode — fill the window (F)'}
      aria-pressed={fullscreen}
      onclick={toggleFullscreen}
    >
      <Icon name={fullscreen ? 'minimize' : 'maximize'} size={14} />
      {fullscreen ? 'Exit focus' : 'Focus'}
    </button>
  </div>
    {/if}
  </div>

  {#if badDataMode}
    <div class="mode-banner" data-testid="bad-data-mode-banner">
      <Icon name="crop" size={14} />
      <span><strong>Marking bad data.</strong> Drag across the trace to mark a range; click a marked range to remove it.</span>
      <button type="button" class="btn btn-sm" data-testid="exit-bad-data-mode-button" onclick={toggleBadDataMode}>
        Done <kbd>Esc</kbd>
      </button>
    </div>
  {/if}

  {#if error}
    <div class="banner banner-error" data-testid="ecg-graph-error">
      <Icon name="alert-circle" size={14} />
      <span>{error}</span>
      <button type="button" class="btn btn-sm" data-testid="dismiss-graph-error" onclick={() => (error = null)}>
        Dismiss
      </button>
    </div>
  {/if}

  <div
    class="ecg-graph-container"
    class:marking={badDataMode}
    class:over-mark={hoveredMarkId !== null}
    data-testid="ecg-graph-container"
    bind:this={containerEl}
    bind:clientWidth={containerWidth}
  ></div>

  {#if badDataToast}
    <div class="graph-toast" data-testid="bad-data-toast" role="status">
      <Icon name="check-circle" size={14} />
      {badDataToast}
    </div>
  {/if}

  <!-- Whole-file overview of the marks: the shading on the chart itself
       (see badDataPlugin) covers the visible range, this covers everything
       off-screen too. -->
  <div class="marks-strip-row">
    <span class="marks-strip-label">
      Bad data
      {#if badDataMarks.length > 0}<span class="marks-count">{badDataMarks.length}</span>{/if}
    </span>
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
    {#if badDataMarks.length === 0}
      <span class="marks-strip-empty">No bad-data ranges marked</span>
    {/if}
  </div>
  </div>

  <!-- Legend doubles as the marker filter: each entry toggles that
       category's visibility on the graph. -->
  <div class="ecg-legend" data-testid="ecg-legend">
    <div class="ecg-legend-categories">
      <span class="legend-total" data-testid="legend-beats-of-interest-count">
        <Icon name="activity" size={12} />
        {beatsOfInterest.length}
        {beatsOfInterest.length === 1 ? 'beat' : 'beats'} of interest
      </span>
      {#each DISPLAY_CATEGORIES as cat (cat)}
        {@const active = !hiddenCategories.has(cat)}
        <button
          type="button"
          class="legend-chip"
          class:legend-chip--inactive={!active}
          data-testid={`legend-toggle-${cat}`}
          aria-pressed={active}
          title={`${active ? 'Hide' : 'Show'} ${DISPLAY_CATEGORY_LABELS[cat]}`}
          onclick={() => toggleCategoryFilter(cat)}
          style={`--legend-color: var(${legendColorVarName(cat)})`}
        >
          <svg class="legend-swatch" width="12" height="12" viewBox="-8 -8 16 16" aria-hidden="true">
            {#if DISPLAY_CATEGORY_SHAPE[cat] === 'circle'}
              <circle cx="0" cy="0" r="6" fill="currentColor" />
            {:else if DISPLAY_CATEGORY_SHAPE[cat] === 'square'}
              <rect x="-5" y="-5" width="10" height="10" fill="currentColor" />
            {:else if DISPLAY_CATEGORY_SHAPE[cat] === 'triangle'}
              <polygon points="0,-7 -6,5 6,5" fill="currentColor" />
            {:else if DISPLAY_CATEGORY_SHAPE[cat] === 'diamond'}
              <polygon points="0,-7 7,0 0,7 -7,0" fill="currentColor" />
            {:else if DISPLAY_CATEGORY_SHAPE[cat] === 'cross'}
              <path
                d="M-6,-3 L-3,-6 L0,-3 L3,-6 L6,-3 L3,0 L6,3 L3,6 L0,3 L-3,6 L-6,3 L-3,0 Z"
                fill="currentColor"
              />
            {:else}
              <rect x="-1.5" y="-6" width="3" height="12" fill="currentColor" />
              <rect x="-6" y="-1.5" width="12" height="3" fill="currentColor" />
            {/if}
          </svg>
          {DISPLAY_CATEGORY_LABELS[cat]}
        </button>
      {/each}
    </div>
    <div class="ecg-legend-review-filter" role="radiogroup" aria-label="Filter by review status">
      <button
        type="button"
        class="btn btn-sm"
        class:btn-active={reviewFilter === 'all'}
        data-testid="review-filter-all"
        onclick={() => setReviewFilter('all')}
      >
        All
      </button>
      <button
        type="button"
        class="btn btn-sm"
        class:btn-active={reviewFilter === 'reviewed'}
        data-testid="review-filter-reviewed"
        onclick={() => setReviewFilter('reviewed')}
      >
        Reviewed
      </button>
      <button
        type="button"
        class="btn btn-sm"
        class:btn-active={reviewFilter === 'unreviewed'}
        data-testid="review-filter-unreviewed"
        onclick={() => setReviewFilter('unreviewed')}
      >
        Unreviewed
      </button>
      <button
        type="button"
        class="btn btn-sm"
        class:btn-active={hideRejected}
        data-testid="hide-rejected-toggle"
        aria-pressed={hideRejected}
        title="Hide beats you have rejected, without deleting them"
        onclick={toggleHideRejected}
      >
        <Icon name="eye-off" size={14} />
        {hideRejected ? 'Rejected hidden' : 'Hide rejected'}
      </button>
    </div>
  </div>
</div>

<style>
  .ecg-graph {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    position: relative;
  }

  .graph-tools {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  /* Focus mode: the graph owns the window, the controls float over it. */
  .ecg-graph.fullscreen {
    position: fixed;
    inset: 0;
    z-index: 60;
    margin: 0;
    padding: var(--space-3);
    background: var(--color-surface);
    overflow: auto;
  }

  .graph-tools.floating {
    position: fixed;
    top: var(--space-3);
    right: var(--space-3);
    z-index: 2;
    max-width: min(760px, calc(100% - var(--space-6)));
    padding: var(--space-2);
    border: 1px solid var(--color-border, #d3dae1);
    border-radius: var(--radius-md, 8px);
    background: var(--color-surface);
    box-shadow: var(--shadow-lg);
    align-items: flex-end;
  }

  .graph-tools.collapsed {
    padding: var(--space-1);
  }

  .tools-bar {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding-bottom: var(--space-1);
    border-bottom: 1px solid var(--color-border, #d3dae1);
    cursor: grab;
    user-select: none;
  }

  .graph-tools.dragging .tools-bar {
    cursor: grabbing;
  }

  .graph-tools.dragging {
    /* No transition while dragging, so the panel tracks the cursor exactly. */
    transition: none;
    opacity: 0.95;
  }

  .tools-grip {
    display: inline-flex;
    color: var(--color-text-muted, #5b6b7c);
  }

  .tools-bar-label {
    flex: 1;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-muted, #5b6b7c);
  }

  .mode-banner {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm, 4px);
    border: 1px solid var(--color-danger, #b91c1c);
    background: var(--color-bad-data-fill, rgba(185, 28, 28, 0.13));
    font-size: var(--font-size-sm);
  }

  .mode-banner span {
    flex: 1;
  }

  .mode-banner kbd {
    padding: 0 4px;
    border: 1px solid currentColor;
    border-radius: 3px;
    font-size: 0.7rem;
    opacity: 0.8;
  }

  .graph-toast {
    position: absolute;
    left: 50%;
    bottom: var(--space-6);
    transform: translateX(-50%);
    z-index: 3;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-radius: 999px;
    background: var(--color-text, #17222e);
    color: var(--color-surface, #fff);
    font-size: var(--font-size-sm);
    box-shadow: var(--shadow-lg);
    pointer-events: none;
  }

  .ecg-graph-container.marking {
    cursor: crosshair;
  }

  .ecg-graph-container.over-mark {
    cursor: pointer;
  }

  .marks-strip-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .marks-strip-label {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    flex-shrink: 0;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-muted, #5b6b7c);
  }

  .marks-count {
    padding: 0 5px;
    border-radius: 999px;
    background: var(--color-danger, #b91c1c);
    color: #fff;
    font-size: 0.65rem;
  }

  .marks-strip-empty {
    padding-left: var(--space-2);
    font-size: 0.7rem;
    color: var(--color-text-muted, #5b6b7c);
  }


  .ecg-graph-toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    align-self: flex-end;
  }

  .beat-nav {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-2);
    border: 1px solid var(--color-border, #d3dae1);
    border-radius: var(--radius-sm, 4px);
    background: var(--color-surface-raised, #f7f9fb);
  }

  .beat-nav-controls {
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }

  .nav-hint {
    font-size: 0.7rem;
    color: var(--color-text-muted, #5b6b7c);
    white-space: nowrap;
  }

  .nav-hint kbd {
    padding: 0 4px;
    border: 1px solid currentColor;
    border-radius: 3px;
    font-family: inherit;
    font-size: 0.65rem;
  }

  .beat-nav-counter {
    min-width: 12rem;
    text-align: center;
    font-size: var(--font-size-sm);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .legend-total {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    font-size: 0.75rem;
    font-weight: 600;
    padding: var(--space-1) var(--space-2);
    border-radius: 999px;
    background: var(--color-accent-soft, #eef1f5);
    color: var(--color-accent, #2563eb);
  }

  .zoom-group {
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 0 var(--space-1);
    border: 1px solid var(--color-border, #d3dae1);
    border-radius: var(--radius-sm, 4px);
  }

  .zoom-group-label {
    font-size: 0.7rem;
    font-weight: 600;
    color: var(--color-text-muted, #5b6b7c);
    padding: 0 2px;
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

  .ecg-legend {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-1) 0;
    border-top: 1px solid var(--color-border, #d3dae1);
  }

  .ecg-legend-categories {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
  }

  .legend-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px var(--space-2) 2px var(--space-1);
    border: 1px solid transparent;
    border-radius: 999px;
    background: none;
    color: var(--color-text, #1e293b);
    font-size: 0.7rem;
    line-height: 1.4;
    transition: opacity var(--transition-fast, 150ms ease), background var(--transition-fast, 150ms ease);
  }

  .legend-chip:hover {
    background: var(--color-surface-hover, #eef1f5);
  }

  .legend-chip--inactive {
    opacity: 0.4;
  }

  .legend-swatch {
    color: var(--legend-color, currentColor);
    flex-shrink: 0;
  }

  .ecg-legend-review-filter {
    display: flex;
    gap: 2px;
    flex-shrink: 0;
  }
</style>
