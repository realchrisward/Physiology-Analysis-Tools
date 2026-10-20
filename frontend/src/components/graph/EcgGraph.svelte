<script module lang="ts">
  import type UplotNS from 'uplot'
  import type { WindowBeat } from '../../lib/api/types'
  import { MARKER_BUCKETS, primaryDisplayCategory } from '../../lib/categories'

  // Beat markers sit on their own fixed-height lane near the top of the plot
  // (a 0..1 scale, see MARKER_LANE_SCALE) instead of riding on the trace, so
  // they read as one horizontal row of ticks and never hide the waveform.
  export const MARKER_LANE_SCALE = 'lane'
  export const MARKER_LANE_Y = 0.94
  // Extra room the y scale leaves above the tallest trace so the lane never
  // overlaps it, as a fraction of the trace's own span.
  export const MARKER_LANE_HEADROOM = 0.22

  // Markers grow as the view zooms in, so they stay easy to see when only a
  // second or two is on screen. 1x at or beyond the reference span, rising by
  // half a step per halving of the visible time, up to MAX.
  const MARKER_ZOOM_REFERENCE_SECONDS = 8
  const MARKER_ZOOM_MAX = 2.5

  export function markerZoomScale(visibleSeconds: number): number {
    if (!Number.isFinite(visibleSeconds) || visibleSeconds <= 0) return 1
    const scale = 1 + 0.5 * Math.log2(MARKER_ZOOM_REFERENCE_SECONDS / visibleSeconds)
    return Math.min(MARKER_ZOOM_MAX, Math.max(1, scale))
  }

  // The view a jump to a beat of interest lands on: the beat itself plus
  // this many PREVIOUS beats, from the recording's own typical RR interval,
  // so a mouse (~0.12 s) and a human (~0.8 s) both see ~5 beats. Clamped so
  // an odd estimate can never give an absurdly tight or huge view, and 2 s
  // when there is no estimate yet. The technician can always zoom out.
  export const FOCUS_PREVIOUS_BEATS = 4
  export const FOCUS_MIN_SECONDS = 0.3
  export const FOCUS_MAX_SECONDS = 15
  export const FOCUS_FALLBACK_SECONDS = 2
  // A beat of interest is reviewed against what came BEFORE it, never the
  // beats after it, so it sits near the right edge with only half an RR
  // interval after it — enough for its own complex, not the next beat's.
  export const FOCUS_TRAILING_FRACTION = 0.5 / (FOCUS_PREVIOUS_BEATS + 1)

  export function focusWidthSeconds(typicalRr: number | null | undefined): number {
    if (typicalRr == null || !Number.isFinite(typicalRr) || typicalRr <= 0) return FOCUS_FALLBACK_SECONDS
    const width = (FOCUS_PREVIOUS_BEATS + 1) * typicalRr
    return Math.min(FOCUS_MAX_SECONDS, Math.max(FOCUS_MIN_SECONDS, width))
  }

  // The [start, end] a view of `width` seconds takes to show `ts` with the
  // beats before it rather than after it.
  export function focusRange(ts: number, width: number): { start: number; end: number } {
    const end = ts + width * FOCUS_TRAILING_FRACTION
    return { start: end - width, end }
  }

  // The view a jump to a flagged beat (FP/FN review) lands on. The model
  // judged that beat from the one before it, itself and the one after it, so
  // unlike a beat of interest the flag sits in the MIDDLE of the view, with
  // this many beats across — from the recording's own typical RR interval,
  // clamped the same way as `focusWidthSeconds`, and a fixed width when there
  // is no estimate yet (it usually isn't known when the graph first opens).
  export const FLAG_FOCUS_BEATS = 7
  export const FLAG_FOCUS_MIN_SECONDS = 0.5
  export const FLAG_FOCUS_MAX_SECONDS = 15
  export const FLAG_FOCUS_FALLBACK_SECONDS = 2
  // A flag's timestamp is one of the detected beats' timestamps, equal to the
  // millisecond; this is how close counts as "the same beat".
  export const FLAG_BEAT_TOLERANCE_SECONDS = 0.002

  export function flagFocusWidth(typicalRr: number | null | undefined): number {
    if (typicalRr == null || !Number.isFinite(typicalRr) || typicalRr <= 0) return FLAG_FOCUS_FALLBACK_SECONDS
    return Math.min(FLAG_FOCUS_MAX_SECONDS, Math.max(FLAG_FOCUS_MIN_SECONDS, FLAG_FOCUS_BEATS * typicalRr))
  }

  // The [start, end] that centres `ts`. `maxWidth` keeps a tighter zoom the
  // technician already chose; `extent` (the file's own range) slides the view
  // back inside the recording for a flag near either end, so the same width
  // is shown rather than a half-empty one.
  export function flagFocusRange(
    ts: number,
    typicalRr: number | null | undefined,
    extent?: { start: number; end: number } | null,
    maxWidth?: number | null,
  ): { start: number; end: number } {
    let width = flagFocusWidth(typicalRr)
    if (maxWidth != null && Number.isFinite(maxWidth) && maxWidth > 0) width = Math.min(width, maxWidth)
    let start = ts - width / 2
    let end = ts + width / 2
    if (extent && extent.end > extent.start) {
      if (width >= extent.end - extent.start) return { start: extent.start, end: extent.end }
      if (start < extent.start) {
        start = extent.start
        end = start + width
      } else if (end > extent.end) {
        end = extent.end
        start = end - width
      }
    }
    return { start, end }
  }

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

      // Every marker shares one fixed lane height. The old approach put each
      // marker on the trace itself, which meant `r_amplitude` (measured on
      // the highpass-filtered signal) could sit far from the R-peak on the
      // raw view, and markers covered the very waveform being judged. On the
      // lane only the x position carries meaning, and that is exact.
      const markerHeight = beat ? MARKER_LANE_Y : null

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
  import { onMount, untrack, type Snippet } from 'svelte'
  import uPlot from 'uplot'
  import 'uplot/dist/uPlot.min.css'
  import { getChannelWindow, getBeatsWindow } from '../../lib/api/windowing'
  import { getReportBeat, getReportWindow } from '../../lib/api/reports'
  import { addBadData, addBeat, deleteBadData, snapBeat, updateBadData } from '../../lib/api/persistence'
  import { themeState } from '../../lib/stores/theme.svelte'
  import { graphPrefs } from '../../lib/stores/graphPrefs.svelte'
  import type { FlaggedTime } from '../../lib/stores/flagProjects.svelte'
  import { FLAG_KIND_COLOR_VAR, FLAG_KIND_LABEL, FLAG_KIND_SOFT_VAR, FLAG_KIND_TITLE, formatFlagTime, formatPrediction } from '../../lib/flags'
  import { shouldIgnoreShortcut } from '../../lib/shortcuts'
  import Icon from '../shared/Icon.svelte'
  import type { BadDataMark, FlagKind, ReportBeat, ReportFilter, ReportLabel } from '../../lib/api/types'
  import {
    REPORT_AGREEMENT_TEXT,
    REPORT_FILTERS,
    REPORT_FILTER_NAME,
    REPORT_FILTER_TITLE,
    REPORT_LABELS,
    REPORT_LABEL_COLOR_VAR,
    REPORT_LABEL_NAME,
    REPORT_LABEL_TITLE,
  } from '../../lib/reports'
  import {
    REPORT_FILTER_STORAGE_KEY,
    REPORT_LANE_HEADROOM,
    REPORT_LANE_STORAGE_KEY,
    REPORT_LANE_Y,
    REPORT_NORMAL_MAX_SPAN_SECONDS,
    REPORT_NORMAL_STORAGE_KEY,
    REPORT_TARGET_TOLERANCE_SECONDS,
    allowsNormalBeats,
    defaultReportFilter,
    findReportBeat,
    indexOfTarget,
    loadStoredBool,
    loadStoredString,
    markKindFor,
    parseStoredReportFilter,
    reportLanePlugin,
    stepTarget,
    storeString,
    type ReportLaneState,
  } from './reportLane'
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
    typicalRr = null,
    focusOverlay,
    onFocusModeChange,
    flaggedTimes = [],
    focusRequest = null,
    onFlagNavigate,
    onBeatClear,
    reportLoaded = false,
    reportRefreshToken = 0,
    reportTargets = {},
    reportBadData = [],
    onReportNavigate,
  }: {
    path: string
    channel: string
    onBeatSelect?: (beat: WindowBeat) => void
    // Bumped by a parent (ReviewWorkspace, after a successful arrhythmia
    // re-run) to request a beats-ONLY re-fetch at the currently visible
    // range — no channel-window re-fetch, no viewport change. See the
    // `$effect` below (mirrors the skip-the-initial-run pattern already
    // used in this file) and
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
    // The recording's typical RR interval in seconds (from the same response
    // as `beatsOfInterest`), used to size the view a jump lands on. Null
    // until known, which falls back to a fixed width.
    typicalRr?: number | null
    // Rendered in a popover beside the selected beat while in focus mode,
    // where the page's side panel is covered by the full-window graph. The
    // parent supplies the content (it owns persistence state); this
    // component only positions it.
    focusOverlay?: Snippet
    // Tells the parent focus mode toggled, so it can render its beat
    // controls in exactly one place (their C/R shortcuts are document-level).
    onFocusModeChange?: (on: boolean) => void
    // FP/FN review: the timestamps a model flagged in THIS recording
    // (`flagsForPath`, ascending by `ts`), each one a detected beat's own
    // timestamp. Every one is highlighted on the trace (see `flagPlugin`) and
    // the "Model flags" navigation steps through them. Empty — the default,
    // and every ordinary recording — means none of that is shown. A live
    // prop: a project can be opened or closed while the graph stays mounted.
    flaggedTimes?: FlaggedTime[]
    // Asks the graph to open on / move to a flag: `ts` is the flag, `seq`
    // distinguishes "the technician asked again" from "nothing new". The
    // graph acts only on a `seq` it has not handled yet — at mount (it opens
    // zoomed on the flag instead of the start of the file) or later (a click
    // on another flag in the sidebar) — never on a `ts` change alone, since
    // the graph's own navigation reports its position back through
    // `onFlagNavigate` and must not be answered by moving again.
    focusRequest?: { ts: number; seq: number } | null
    // The graph's own flag navigation (the buttons, `[` / `]`) landed on the
    // flag at `ts`; the parent records it so the sidebar can highlight it.
    onFlagNavigate?: (ts: number) => void
    // Asks the parent to drop its selected beat. A jump to a flag whose own
    // beat cannot be found (another channel or beat settings, a deleted beat,
    // detection not done yet) must not leave the PREVIOUS selection active:
    // the view has moved on, but the confirm/reject keys would still act on
    // that old, off-screen beat.
    onBeatClear?: () => void
    // Report labels: an imported xlsx report attached to this recording, shown
    // read-only under the app's own marker lane (see `reportLane.ts`). False —
    // the default, and every ordinary recording — means no report request is
    // ever made and nothing of it is drawn. A live prop: a report can be
    // loaded, replaced or removed while the graph stays mounted.
    reportLoaded?: boolean
    // Bumped by the parent when the report window has to be refetched for what
    // is already on screen: the report was loaded/replaced/removed, or this
    // app's own beats changed (matching and agreement depend on them). Same
    // skip-the-first-value, react-to-changes shape as `beatsRefreshToken`;
    // refetches the report ONLY — no trace refetch, no viewport change.
    reportRefreshToken?: number
    // The report navigator's targets per filter — the report's own timestamps,
    // ascending, for the WHOLE file (from `GET /reports/navigation`).
    reportTargets?: Partial<Record<ReportFilter, number[]>>
    // The report's own bad-data ranges, hatched behind the trace so they read
    // differently from this app's red marks.
    reportBadData?: { start: number; stop: number }[]
    // The graph's own report navigation (the buttons, `,` / `.`) landed on the
    // report beat at `ts`.
    onReportNavigate?: (ts: number) => void
  } = $props()

  const DEBOUNCE_MS = 150
  const HEIGHT = 380
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
  // How close (px) a press must be to the selected bad-data range's edge to
  // grab that edge and resize the range rather than draw a new one.
  const MARK_EDGE_HIT_PX = 8
  // Width of the view the graph zooms to around a beat being added, so the
  // technician can judge (and correct) the suggested position.
  const ADD_BEAT_ZOOM_SECONDS = 1
  // A page-forward/back step moves by slightly less than a full screen so a
  // sliver of the previous view stays visible as a visual anchor, the same
  // way a document reader's page-down does.
  const PAGE_OVERLAP_RATIO = 0.9
  const TOOLS_POS_KEY = 'pat.graphToolsPos'
  // Floor for the point density we ask the backend for, and the width to
  // assume when the container hasn't been laid out yet. Both exist so a
  // zero-width first measurement can never produce a near-empty trace.
  const MIN_RESOLUTION = 400
  const FALLBACK_WIDTH = 900
  // Refetch once the container is this much wider than the width the
  // current data was fetched for — the data's point density is tied to
  // pixel width, so growing the graph (sidebar collapse, window resize,
  // focus mode) genuinely needs more points, not just a rescale.
  const WIDTH_GROWTH_REFETCH_RATIO = 1.5
  const RETRY_DELAY_MS = 700

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

  // Marker size multiplier for the view currently on screen (see markerZoomScale).
  function visibleMarkerScale(u: uPlot): number {
    const { min, max } = u.scales.x
    return min != null && max != null ? markerZoomScale(max - min) : 1
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
      const r = (pxSize * visibleMarkerScale(u)) / 2
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
      scale: MARKER_LANE_SCALE,
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
      scale: MARKER_LANE_SCALE,
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
      scale: MARKER_LANE_SCALE,
      points: {
        show: true,
        size,
        width: 2,
        stroke: () => cssVar('--color-danger', '#b91c1c'),
        paths: (u: uPlot, seriesIdx: number, idx0: number, idx1: number) => {
          const path = new Path2D()
          const yData = u.data[seriesIdx] as (number | null)[]
          const xData = u.data[0] as number[]
          const r = (size * visibleMarkerScale(u)) / 2
          for (let i = idx0; i <= idx1; i++) {
            const xVal = xData[i]
            const yVal = yData[i]
            if (xVal == null || yVal == null) continue
            const cx = u.valToPos(xVal, 'x', true)
            const cy = u.valToPos(yVal, MARKER_LANE_SCALE, true)
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
    return HEIGHT
  }

  function toggleFullscreen() {
    fullscreen = !fullscreen
    if (!fullscreen) toolsCollapsed = false
  }

  $effect(() => {
    onFocusModeChange?.(fullscreen)
  })

  // Focus-mode beat popover: anchored beside the selected beat's x position
  // and re-anchored whenever the chart redraws (pan, zoom, resize).
  const OVERLAY_WIDTH_PX = 320
  let overlayPos: { left: number; top: number } | null = $state(null)
  let overlayDismissedFor: number | null = $state(null)

  function updateOverlayPos() {
    if (!chart || !fullscreen || selectedBeatTs === null || !containerEl) {
      if (overlayPos !== null) overlayPos = null
      return
    }
    const over = chart.over
    const x = chart.valToPos(selectedBeatTs, 'x')
    if (!Number.isFinite(x) || x < 0 || x > over.clientWidth) {
      if (overlayPos !== null) overlayPos = null
      return
    }
    const base = containerEl.offsetLeft + over.offsetLeft
    const fitsRight = over.clientWidth - x > OVERLAY_WIDTH_PX + 24
    const left = Math.max(0, fitsRight ? base + x + 16 : base + x - OVERLAY_WIDTH_PX - 16)
    const top = containerEl.offsetTop + over.offsetTop + 8
    if (overlayPos?.left === left && overlayPos?.top === top) return
    overlayPos = { left, top }
  }

  function overlayAnchorPlugin(): uPlot.Plugin {
    return { hooks: { draw: () => updateOverlayPos() } }
  }

  $effect(() => {
    fullscreen
    selectedBeatTs
    updateOverlayPos()
  })

  let showFocusOverlay = $derived(
    fullscreen &&
      focusOverlay !== undefined &&
      overlayPos !== null &&
      selectedBeatTs !== null &&
      overlayDismissedFor !== selectedBeatTs,
  )

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
  // Set once the technician zooms Y by hand, so a later data refetch does
  // not silently re-range Y underneath them. Cleared by Reset view.
  let yManuallyZoomed = false
  // Toggled by the maximize/minimize button in the toolbar; taller chart
  // height for a closer look at dense waveforms. See the `$effect` below
  // that applies it to the live chart.

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
  // The flag (`ts`) the graph was last focused on — by the "Model flags"
  // navigation, or by a `focusRequest` — or null before any. Kept as a `ts`
  // rather than an index into `flaggedTimes` so it stays right if that list
  // changes underneath it (a project closed, another opened). Drawn heavier
  // than the other flags, and what the navigation steps from.
  let currentFlagTs: number | null = $state(null)
  let flagIndex: number = $derived(indexOfFlagTime(flaggedTimes, currentFlagTs))
  let currentFlag: FlaggedTime | null = $derived(flagIndex >= 0 ? flaggedTimes[flagIndex] : null)
  // `seq` of the last `focusRequest` acted on, so each one is honoured once.
  // Plain, not `$state`: only read from the mount path and the effect below.
  let handledFocusSeq: number | null = null
  // The flag whose beat the graph still has to select: set when a jump
  // focuses a flag, cleared once that beat has been selected (right away, after
  // the jump's fetch, or later when detection delivers beats) or the
  // technician chooses a beat themselves. Plain: never read by the template.
  let pendingFlagSelectTs: number | null = null
  // Bumped by anything that decides what is selected — a flag jump, a beat of
  // interest jump, a marker click — so a jump that was waiting on its fetch can
  // tell it has been superseded and must not select (or clear) afterwards.
  let selectionEpoch = 0
  // True while the current flag has no beat in the loaded detection result:
  // another channel or beat settings, a deleted beat, or detection not
  // finished yet. Shown next to the flag counter.
  let flagBeatMissing: boolean = $state(false)

  // ---- Report labels state (see `reportLane.ts`) -------------------------------
  // The legend's switch for the report lane (and the disagreement bands and
  // bad-data hatching that belong to it). Hiding it leaves the navigation
  // working. Remembered across channel switches, which remount this component.
  let showReportLane: boolean = $state(loadStoredBool(REPORT_LANE_STORAGE_KEY, true))
  // Normal (label 0) report beats are nearly every beat, so they are fetched
  // and drawn only on request.
  let showReportNormal: boolean = $state(loadStoredBool(REPORT_NORMAL_STORAGE_KEY, false))
  // True while a report is attached and its lane is switched on: what decides
  // whether the y scale makes room for the second lane.
  let reportLaneActive: boolean = $derived(reportLoaded && showReportLane)
  // The technician's own filter choice (remembered), else a default taken from
  // what the report has to show: disagreements when there are any.
  let reportFilterChoice: ReportFilter | null = $state(
    parseStoredReportFilter(loadStoredString(REPORT_FILTER_STORAGE_KEY), REPORT_FILTERS),
  )
  let reportFilter: ReportFilter = $derived(reportFilterChoice ?? defaultReportFilter(reportTargets))
  let reportFilterTargets: number[] = $derived(reportTargets?.[reportFilter] ?? [])
  // The report beat (`ts`) the navigator was last on, or null before any. A
  // `ts` rather than an index so it stays right when the targets change
  // underneath it (our beats were edited, a report was replaced).
  let currentReportTs: number | null = $state(null)
  let reportIndex: number = $derived(indexOfTarget(reportFilterTargets, currentReportTs))
  // True while the current report beat has no beat of ours to select (the
  // report beat was not matched to any detected beat).
  let reportBeatUnmatched: boolean = $state(false)
  // A failed report fetch is never an error of the trace: the lane just stays
  // empty, and this says why.
  let reportFetchFailed: boolean = $state(false)
  // True while "show normal beats" is on but the window on screen is too wide
  // to have them fetched (the legend then says to zoom in).
  let reportNormalSkipped: boolean = $state(false)
  // The report beats of the window on screen (the plugin's data). Plain, like
  // `lastBeats`: only drawing and event handlers read it.
  let lastReportBeats: ReportBeat[] = []
  // Bumped whenever something a report fetch depends on changes (report
  // loaded/removed/replaced, our beats changed, normal beats shown or hidden):
  // a fetch that began under an older value is stale when it lands and is
  // followed by a fresh report-only fetch.
  let reportEpoch = 0
  // Ordering guard for report-only refreshes, like `beatsRefreshId`.
  let reportRefreshId = 0
  // The report beat whose matched beat of ours the graph still has to select —
  // `pendingFlagSelectTs`'s counterpart for the report navigator.
  let pendingReportSelectTs: number | null = null
  // The values the inputs of a report refresh had last time the effect ran.
  let previousReportInputs: { loaded: boolean; token: number; normal: boolean } | undefined
  let previousReportLaneActive: boolean | undefined
  // `chart` itself is a plain (non-reactive) binding, so markup that only
  // read it would never update once the async mount finally created it.
  // This is the reactive "the chart exists now" signal.
  let chartReady: boolean = $state(false)
  // True while the first load (or a retry of it) is in flight, so the graph
  // area says "loading" rather than sitting blank.
  let loading: boolean = $state(false)

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
  // The mark being edited (chosen by clicking it on the chart or the strip).
  // Its range shows in an editor row with numeric fields, Save and Delete,
  // and its edges can be dragged in bad-data mode.
  let selectedMarkId: number | null = $state(null)
  let selectedMark: BadDataMark | null = $derived(
    badDataMarks.find((m) => m.id === selectedMarkId) ?? null,
  )
  let editStart: string = $state('')
  let editStop: string = $state('')
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
  // Drives the bottom X-range slider: kept in sync with `chart.scales.x` by
  // a `setScale` hook (see `panZoomPlugin`) so it reflects every way the
  // visible range can change — drag, wheel, zoom buttons, page/jump/focus —
  // not just moves made through the slider itself.
  let sliderCenter: number = $state(0)
  let sliderHalfWidth: number = $state(0)
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
  // The x-range `lastBeats` was fetched for. A flag outside it says nothing
  // about whether that flag has a detected beat (see `settleFlagBeat`).
  let lastBeatsRange: { start: number; end: number } | undefined
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
  // no-op (same discipline as the other skip-the-initial-run guards).
  let previousBeatsRefreshToken: number | undefined
  // Same skip-the-initial-run discipline for the raw/filtered toggle.
  let previousShowFiltered: boolean | undefined

  // The x-range actually LOADED into the chart right now (normally wider
  // than what's visible — see PAD_FACTOR below), and the visible-range
  // width that was requested when it was fetched (used to detect "zoomed
  // in enough since then that this buffer's point density is now too
  // coarse"). `undefined` until the first fetch (mount) completes.
  let loadedRange:
    | { start: number; end: number; requestedWidth: number; pixelWidth: number }
    | undefined
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
  // Ordering guard for channel refetches: each captures the value and only
  // applies its result if it is still the newest, so a slow response can
  // never overwrite a newer one's data.
  let dataRequestId = 0
  // How many channel refetches are in flight. A beats-only refresh and the
  // selection-highlight re-merge both rebuild the chart from
  // `lastChannelWindow`, which a pending refetch is about to replace — so
  // while one is running they stand aside rather than painting the previous
  // window's waveform underneath the new scale. Nothing is lost: a refetch
  // fetches beats as well, and re-reads the current selection when it
  // merges.
  let channelFetchInFlight = 0
  // Separate ordering guard for beats-only refreshes, so one can never
  // cancel a channel refetch (they are not interchangeable — only the
  // refetch carries waveform data for the new range).
  let beatsRefreshId = 0

  // The requested resolution is a PIXEL count: the backend downsamples to
  // roughly 2 points per unit of it. A container that has not been laid out
  // yet reports clientWidth 0, which asked for resolution 1 and got the
  // whole recording flattened to ~4 points — the "graph only partially
  // loads" report. Flooring it means a mistimed first measurement costs a
  // slightly coarse first paint (corrected by the width effect below)
  // instead of an unusable one.
  function resolutionFor(width: number): number {
    return Math.max(MIN_RESOLUTION, Math.round(width))
  }

  /** Container width, falling back to a sane default before layout. */
  function measuredWidth(): number {
    const measured = containerEl?.clientWidth ?? 0
    return measured > 0 ? measured : FALLBACK_WIDTH
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

  // The report's beats for [start, end], matched against the recording's
  // current beats by the backend. Asked for only while a report is attached
  // (an ordinary recording makes no report request at all), and it can never
  // fail the caller: like a beats failure it only means nothing is drawn for
  // that range — but unlike one it is reported (`failed`) so the legend can
  // say why the lane is empty. `epoch` is what `reportEpoch` was when the
  // request was made (see there). Normal beats are asked for only while the
  // window is narrow enough (see REPORT_NORMAL_MAX_SPAN_SECONDS);
  // `normalSkipped` says they were wanted but the window was too wide.
  type ReportFetch = { beats: ReportBeat[]; failed: boolean; epoch: number; normalSkipped: boolean }

  async function loadReportBeats(start: number, end: number): Promise<ReportFetch> {
    const epoch = reportEpoch
    if (!reportLoaded) return { beats: [], failed: false, epoch, normalSkipped: false }
    const wantNormal = showReportNormal
    const includeNormal = wantNormal && allowsNormalBeats(start, end)
    const normalSkipped = wantNormal && !includeNormal
    try {
      const result = await getReportWindow(path, start, end, includeNormal)
      if (result && result.status === 'ok' && !result.error && Array.isArray(result.beats)) {
        return { beats: result.beats, failed: false, epoch, normalSkipped }
      }
    } catch {
      // fall through: no report lane for this range
    }
    return { beats: [], failed: true, epoch, normalSkipped }
  }

  // Makes a fetched report window the one on screen. Called only by a caller
  // that has confirmed its own request is still the current one.
  function commitReportFetch(report: ReportFetch) {
    // The report was removed while this request was in flight.
    if (!reportLoaded) {
      lastReportBeats = []
      reportFetchFailed = false
      reportNormalSkipped = false
      return
    }
    lastReportBeats = report.beats
    reportFetchFailed = report.failed
    reportNormalSkipped = report.normalSkipped
  }

  // Whether a report fetch that just landed has to be followed by a fresh one
  // for the window now loaded: something it depends on changed while it was in
  // flight, or the normal beats were left out of a window (the whole-file first
  // fetch is always "too wide") that turns out narrow enough to have them.
  function reportNeedsRefetch(report: ReportFetch): boolean {
    if (!reportLoaded) return false
    if (report.epoch !== reportEpoch) return true
    return report.normalSkipped && loadedRange !== undefined && allowsNormalBeats(loadedRange.start, loadedRange.end)
  }

  async function fetchMergedWindow(
    start: number,
    end: number,
    width: number,
  ): Promise<{
    channel: { x: number[]; y: number[] }
    beats: WindowBeat[]
    merged: BeatSeriesData
    report: ReportFetch
  } | null> {
    const channelData = await loadWindow(start, end, width)
    if (!channelData) return null
    const beats = await loadBeats(start, end)
    // After the beats, so the backend matches the report against beats at
    // least as fresh as the ones about to be drawn next to it.
    const report = await loadReportBeats(start, end)
    // `lastChannelWindow`/`lastBeats`/`lastReportBeats` are assigned by the
    // caller, after its staleness check — committing them here would let a
    // superseded fetch leave the retained state describing a window that is no
    // longer shown.
    return {
      channel: channelData,
      beats,
      merged: buildBeatAlignedData(channelData.x, channelData.y, beats, selectedBeatTs, hideRejected),
      report,
    }
  }

  // Re-fetches ONLY `/reports/window` for the range the chart currently has
  // loaded and redraws — no trace or beats request, no viewport change.
  // Triggered by `reportRefreshToken`, the report being attached or removed,
  // and the "show normal beats" switch (see the `$effect` below). Unlike
  // `refreshBeatsOnly` it does not stand aside for a refetch in flight: that
  // refetch may have asked for the report before whatever changed, and
  // answers it again afterwards if so (see `refetch`).
  async function refreshReportOnly() {
    if (!chart || !loadedRange || mountCancelled) return
    const requestId = ++reportRefreshId
    if (!reportLoaded) {
      commitReportFetch({ beats: [], failed: false, epoch: reportEpoch, normalSkipped: false })
      chart.redraw()
      return
    }
    const range = { start: loadedRange.start, end: loadedRange.end }
    const report = await loadReportBeats(range.start, range.end)
    // Superseded by a newer refresh, or the graph is gone (unmounted, or
    // remounted for another channel — that instance has its own state).
    if (requestId !== reportRefreshId || mountCancelled || !chart) return
    // A refetch moved the loaded window meanwhile: it carries its own report.
    if (!loadedRange || loadedRange.start !== range.start || loadedRange.end !== range.end) return
    commitReportFetch(report)
    settleReportBeat()
    chart.redraw()
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

    // A refetch already on its way will deliver fresh beats along with the
    // waveform for the range actually being shown, so stand aside.
    if (channelFetchInFlight > 0) return

    const requestId = ++beatsRefreshId
    const beats = await loadBeats(min, max)
    if (requestId !== beatsRefreshId || channelFetchInFlight > 0) return
    if (!chart || !lastChannelWindow) return

    lastBeats = beats
    lastBeatsRange = { start: min, end: max }
    const merged = buildBeatAlignedData(lastChannelWindow.x, lastChannelWindow.y, beats, selectedBeatTs, hideRejected)
    chart.setData(toChartData(merged), false)
    applyFilters()
    // Detection may only just have produced the beats the open flag waits on.
    settleFlagBeat()
    settleReportBeat()
  }

  // Re-applies just the selection-highlight ring against whatever waveform
  // window is currently on screen, without any network round trip — beats
  // are cheap to have retained (`lastBeats`) and re-merging is pure local
  // computation. Guarded on `chart`/`lastChannelWindow` existing, same as
  // `refreshBeatsOnly` above, so it's a safe no-op before the first mount
  // fetch has resolved.
  function applySelectionHighlight() {
    if (!chart || !lastChannelWindow) return
    // See `channelFetchInFlight`: the pending refetch re-reads the current
    // selection when it merges, so it will paint this anyway.
    if (channelFetchInFlight > 0) return
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
    // The graph got materially wider than when this data was fetched, so
    // the point density it was fetched at is now too coarse for the pixels
    // available. Checked first because it is independent of the x-range:
    // the two checks below only compare data-space widths and would happily
    // keep serving a 4-point trace forever.
    if (measuredWidth() > loadedRange.pixelWidth * WIDTH_GROWTH_REFETCH_RATIO) return true

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
    options: { pad?: boolean; reRange?: boolean } = {},
  ) {
    const requestId = ++dataRequestId
    channelFetchInFlight += 1
    const usePad = options.pad ?? true
    const requestedWidth = max - min
    const pad = usePad ? requestedWidth * PAD_FACTOR : 0
    const fetchStart = min - pad
    const fetchEnd = max + pad
    const baseWidth = measuredWidth()
    const fetchWidth = usePad ? baseWidth * (1 + 2 * PAD_FACTOR) : baseWidth

    let result
    try {
      result = await fetchMergedWindow(fetchStart, fetchEnd, fetchWidth)
    } finally {
      channelFetchInFlight -= 1
    }
    // Superseded by a newer refetch (a later pan/zoom, or a Reset) dispatched
    // while this one was in flight — see `dataRequestId`'s own comment
    // above for why applying a stale response here would be a real bug
    // (the "reset zooms out too much until a second click" report).
    if (requestId !== dataRequestId) return

    if (result && chart) {
      const { channel: channelData, beats, merged, report } = result
      // Commit the retained window/beats only now that this response is
      // confirmed current — these drive click hit-testing and every later
      // local re-merge, so stale values here cause silently wrong behaviour
      // rather than a visible glitch.
      lastChannelWindow = channelData
      lastBeats = beats
      lastBeatsRange = { start: fetchStart, end: fetchEnd }
      commitReportFetch(report)
      // uPlot derives the y range from the data it holds AT THE MOMENT the
      // x scale changes. Every jump/zoom sets x first and only then fetches
      // that window's data, so y ends up ranged against the PREVIOUS
      // window — and handing setData `false` (don't touch scales) meant it
      // was never revisited. The new trace then drew outside the visible y
      // range: a plot that looks empty or half-drawn until some later
      // interaction happens to re-range it. Re-ranging here and re-pinning
      // x to the exact requested window is the supported way to fix it
      // (uPlot normalises `Scale.auto` into an internal function at
      // construction, so assigning `chart.scales.y.auto = true` corrupts it
      // instead of re-enabling auto-ranging).
      //
      // Skipped while the technician has taken manual control of the y zoom,
      // and during a live pan drag (`maybeBufferAhead`), where re-pinning x
      // would fight the drag's own scale updates.
      const reRange = (options.reRange ?? true) && !yManuallyZoomed
      chart.setData(toChartData(merged), reRange)
      if (reRange) {
        chart.setScale('x', { min, max })
      }
      loadedRange = { start: fetchStart, end: fetchEnd, requestedWidth, pixelWidth: fetchWidth }
      applyFilters() // setData resets series visibility; re-apply the active category/review filters
      settleFlagBeat()
      settleReportBeat()
      // Whatever the report depends on changed while this was in flight, so
      // its report beats are out of date: ask again for the window now shown.
      if (reportNeedsRefetch(report)) void refreshReportOnly()
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
    void refetch(min, max, { reRange: false }).finally(() => {
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

  // Moves a mark to a new range (the backend re-sorts the bounds).
  async function updateBadDataMark(mark: BadDataMark, start: number, stop: number) {
    const result = await updateBadData(path, mark.id, start, stop)
    if (result.status === 'ok' && result.mark && !result.error) {
      const updated = result.mark
      badDataMarks = badDataMarks.map((m) => (m.id === updated.id ? updated : m))
      chart?.redraw()
      showBadDataToast(`Bad-data range now ${updated.start.toFixed(2)}–${updated.stop.toFixed(2)}s`)
    } else {
      error = result.error ?? 'Failed to update bad data mark'
    }
  }

  function selectMark(mark: BadDataMark | null) {
    selectedMarkId = mark?.id ?? null
    chart?.redraw()
  }

  // Keeps the editor fields in step with whichever mark is selected (and
  // with its saved range after an edge drag or save).
  $effect(() => {
    if (selectedMark) {
      editStart = selectedMark.start.toFixed(3)
      editStop = selectedMark.stop.toFixed(3)
    }
  })

  let editRangeValid = $derived(
    Number.isFinite(Number(editStart)) &&
      Number.isFinite(Number(editStop)) &&
      editStart.trim() !== '' &&
      editStop.trim() !== '' &&
      Number(editStart) !== Number(editStop),
  )

  function saveEditedRange() {
    if (!selectedMark || !editRangeValid) return
    void updateBadDataMark(selectedMark, Number(editStart), Number(editStop))
  }

  async function removeBadDataMark(mark: BadDataMark) {
    const result = await deleteBadData(path, mark.id)
    if (result.status === 'ok' && !result.error) {
      badDataMarks = badDataMarks.filter((m) => m.id !== mark.id)
      if (hoveredMarkId === mark.id) hoveredMarkId = null
      if (selectedMarkId === mark.id) selectedMarkId = null
      showBadDataToast('Bad-data mark removed')
    } else {
      error = result.error ?? 'Failed to remove bad data mark'
    }
  }

  function toggleBadDataMode() {
    badDataMode = !badDataMode
    if (!badDataMode) hoveredMarkId = null
    if (badDataMode) leaveAddBeatMode()
  }

  // Add-beat mode: click near a beat the detector missed. The graph asks the
  // backend for the nearest signal peak, zooms there and shows it as a
  // pending beat. Clicking elsewhere while it is pending moves it to exactly
  // where you clicked (the zoomed-in override of the automatic snap); nothing
  // is saved until the technician confirms.
  let addBeatMode: boolean = $state(false)
  let pendingBeat: { ts: number; source: 'snapped' | 'manual' } | null = $state(null)
  let addBeatBusy: boolean = $state(false)
  let addBeatError: string | null = $state(null)

  function leaveAddBeatMode() {
    addBeatMode = false
    pendingBeat = null
    addBeatError = null
    chart?.redraw()
  }

  function toggleAddBeatMode() {
    if (addBeatMode) {
      leaveAddBeatMode()
      return
    }
    addBeatMode = true
    badDataMode = false
    hoveredMarkId = null
    selectedMarkId = null
  }

  async function snapPendingTo(ts: number) {
    addBeatBusy = true
    addBeatError = null
    const result = await snapBeat(path, channel, ts)
    addBeatBusy = false
    if (result.status === 'ok' && result.ts !== null && !result.error) {
      pendingBeat = { ts: result.ts, source: 'snapped' }
      const { min, max } = chart?.scales.x ?? {}
      const currentWidth = min != null && max != null ? max - min : ADD_BEAT_ZOOM_SECONDS
      const width = Math.min(currentWidth, ADD_BEAT_ZOOM_SECONDS)
      await setVisibleRange(result.ts - width / 2, result.ts + width / 2)
      chart?.redraw()
    } else {
      addBeatError = result.error ?? 'Could not find a peak here'
    }
  }

  async function handleAddBeatClick(u: uPlot, upEvent: MouseEvent) {
    if (addBeatBusy) return
    const rect = u.over.getBoundingClientRect()
    const xVal = u.posToVal(upEvent.clientX - rect.left, 'x')
    if (pendingBeat) {
      pendingBeat = { ts: xVal, source: 'manual' }
      addBeatError = null
      chart?.redraw()
      return
    }
    await snapPendingTo(xVal)
  }

  async function confirmAddBeat() {
    if (!pendingBeat || addBeatBusy) return
    addBeatBusy = true
    addBeatError = null
    const result = await addBeat(path, channel, pendingBeat.ts)
    addBeatBusy = false
    if (result.status === 'ok' && result.beat && !result.error) {
      const added = result.beat
      pendingBeat = null
      noteManualSelection()
      showBadDataToast(
        `Beat added at ${added.ts.toFixed(3)}s — re-run arrhythmia detection to include it in the flags`,
      )
      await refreshBeatsOnly()
      const loaded = lastBeats.find((b) => b.ts === added.ts)
      if (loaded) onBeatSelect?.(loaded)
      chart?.redraw()
    } else {
      addBeatError = result.error ?? 'Could not add the beat'
    }
  }

  // The pending beat, drawn as a dashed guide through the whole trace plus a
  // ring on the marker lane, so it is obvious which peak will be added.
  function pendingBeatPlugin(): uPlot.Plugin {
    return {
      hooks: {
        draw: (u) => {
          if (!pendingBeat) return
          const x = u.valToPos(pendingBeat.ts, 'x', true)
          if (!Number.isFinite(x) || x < u.bbox.left || x > u.bbox.left + u.bbox.width) return
          const { ctx } = u
          try {
            const accent = cssVar('--color-accent', '#2563eb')
            ctx.save()
            ctx.strokeStyle = accent
            ctx.fillStyle = accent
            ctx.lineWidth = 2
            ctx.setLineDash([6, 4])
            ctx.beginPath()
            ctx.moveTo(x, u.bbox.top)
            ctx.lineTo(x, u.bbox.top + u.bbox.height)
            ctx.stroke()
            ctx.setLineDash([])
            const y = u.valToPos(MARKER_LANE_Y, MARKER_LANE_SCALE, true)
            ctx.beginPath()
            ctx.arc(x, y, 10, 0, Math.PI * 2)
            ctx.stroke()
            ctx.beginPath()
            ctx.moveTo(x, y - 5)
            ctx.lineTo(x + 5, y)
            ctx.lineTo(x, y + 5)
            ctx.lineTo(x - 5, y)
            ctx.closePath()
            ctx.fill()
            ctx.restore()
          } catch {
            // decoration only — never take interaction down with it
          }
        },
      },
    }
  }

  $effect(() => {
    pendingBeat
    chart?.redraw()
  })

  // ---- FP/FN review flags ------------------------------------------------
  // `flaggedTimes` are drawn on the chart canvas itself (like the bad-data
  // shading and the pending beat), so they stay aligned with the trace at any
  // pan/zoom and cost no DOM. Per flag: a soft band and a full-height line in
  // the flag's kind color, plus a small tag. The tag hangs just BELOW the
  // beat-marker lane rather than at the very top: the lane is one continuous
  // row of ticks that must stay readable, markers grow when zoomed in, and
  // the headroom under the lane (see MARKER_LANE_HEADROOM) is the one band
  // the trace never reaches — so a tag there covers neither markers nor
  // waveform. The CURRENT flag (see `currentFlagTs`) is drawn heavier, with a
  // solid tag.
  const FLAG_BAND_PX = 6
  const FLAG_BAND_CURRENT_PX = 8
  const FLAG_LINE_PX = 1.5
  const FLAG_LINE_CURRENT_PX = 2.5
  const FLAG_TAG_FONT_PX = 10
  const FLAG_TAG_HEIGHT_PX = 13
  const FLAG_TAG_PAD_X_PX = 4
  const FLAG_TAG_GAP_PX = 4

  // Fallbacks for when the custom properties can't be resolved (jsdom), the
  // light-theme values from tokens.css.
  const FLAG_FALLBACK_COLOR: Record<FlagKind, string> = { fp: '#ea580c', fn: '#0891b2', flag: '#a16207' }
  const FLAG_FALLBACK_SOFT: Record<FlagKind, string> = {
    fp: 'rgba(234, 88, 12, 0.14)',
    fn: 'rgba(8, 145, 178, 0.14)',
    flag: 'rgba(161, 98, 7, 0.14)',
  }

  function isSameFlagTime(a: number, b: number): boolean {
    return Math.abs(a - b) < FLAG_BEAT_TOLERANCE_SECONDS
  }

  // Why a flag can lack a detected beat (see `flagBeatMissing`).
  const FLAG_NO_BEAT_TITLE =
    'No beat was detected at this flag, so no beat is selected here. The model may have judged another channel or other beat-detection settings, the beat may have been deleted, or detection may not have finished yet (the beat is selected as soon as it appears).'

  // With more flags than this inside the view, tags are skipped (only the
  // current flag keeps its own): they could not be told apart or read at that
  // density anyway, and they are the costly part of a redraw. Bands and lines
  // are still drawn for every flag.
  const FLAG_TAG_MAX_VISIBLE = 150

  function flagColor(kind: FlagKind): string {
    return cssVar(FLAG_KIND_COLOR_VAR[kind], FLAG_FALLBACK_COLOR[kind])
  }

  function flagSoftColor(kind: FlagKind): string {
    return cssVar(FLAG_KIND_SOFT_VAR[kind], FLAG_FALLBACK_SOFT[kind])
  }

  // `resolve` once per kind for the lifetime of the returned function. A draw
  // makes one of these per color it needs, so a redraw costs a computed-style
  // lookup per KIND (at most three), not one per flag.
  function perKind(resolve: (kind: FlagKind) => string): (kind: FlagKind) => string {
    const cache: Partial<Record<FlagKind, string>> = {}
    return (kind) => (cache[kind] ??= resolve(kind))
  }

  // Text color for a solid tag: the flag colors run from near-black (black &
  // white theme) to bright yellow (dark theme), so neither white nor dark text
  // works on all of them.
  function readableTextOn(color: string): string {
    const rgb = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(hexToRgba(color, 1))
    if (!rgb) return '#ffffff'
    const luma = (0.299 * Number(rgb[1]) + 0.587 * Number(rgb[2]) + 0.114 * Number(rgb[3])) / 255
    return luma > 0.6 ? '#111827' : '#ffffff'
  }

  // The flags inside the visible x range (plus `marginPx` of slack for line
  // width), left to right, with their x position in canvas pixels and whether
  // each is the current flag (by identity: flags within the tolerance of each
  // other are still separate flags).
  function visibleFlags(u: uPlot, marginPx: number): { flag: FlaggedTime; x: number; current: boolean }[] {
    const visible: { flag: FlaggedTime; x: number; current: boolean }[] = []
    const { min, max } = u.scales.x
    if (min == null || max == null || !(max > min)) return visible
    const left = u.bbox.left - marginPx
    const right = u.bbox.left + u.bbox.width + marginPx
    const current = currentFlag
    for (const flag of flaggedTimes) {
      if (flag.ts < min || flag.ts > max) continue
      const x = u.valToPos(flag.ts, 'x', true)
      if (Number.isFinite(x) && x >= left && x <= right) visible.push({ flag, x, current: flag === current })
    }
    return visible
  }

  function flagPlugin(): uPlot.Plugin {
    return {
      hooks: {
        // Behind the waveform, like the bad-data shading, so the band never
        // tints the trace being judged.
        drawClear: (u) => {
          if (flaggedTimes.length === 0) return
          const { ctx } = u
          // Decoration only — never take interaction down with it.
          try {
            const dpr = uPlot.pxRatio || 1
            ctx.save()
            try {
              ctx.beginPath()
              ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height)
              ctx.clip()
              const softOf = perKind(flagSoftColor)
              for (const { flag, x, current } of visibleFlags(u, 0)) {
                const half = ((current ? FLAG_BAND_CURRENT_PX : FLAG_BAND_PX) * dpr) / 2
                ctx.fillStyle = softOf(flag.kind)
                ctx.fillRect(x - half, u.bbox.top, half * 2, u.bbox.height)
              }
            } finally {
              ctx.restore()
            }
          } catch {
            // ignore — see above
          }
        },
        // On top of the series: the line and the tag.
        draw: (u) => {
          if (flaggedTimes.length === 0) return
          const { ctx } = u
          try {
            const dpr = uPlot.pxRatio || 1
            const visible = visibleFlags(u, 2 * dpr)
            if (visible.length === 0) return
            ctx.save()
            try {
              const { left, top, width, height } = u.bbox
              ctx.beginPath()
              ctx.rect(left, top, width, height)
              ctx.clip()

              const colorOf = perKind(flagColor)
              ctx.lineCap = 'butt'
              for (const { flag, x, current } of visible) {
                ctx.strokeStyle = colorOf(flag.kind)
                ctx.lineWidth = (current ? FLAG_LINE_CURRENT_PX : FLAG_LINE_PX) * dpr
                ctx.beginPath()
                ctx.moveTo(x, top)
                ctx.lineTo(x, top + height)
                ctx.stroke()
              }

              // Past the density limit only the current flag keeps a tag.
              const showAllTags = visible.length <= FLAG_TAG_MAX_VISIBLE
              if (!showAllTags && !visible.some((v) => v.current)) return

              // Tags sit under the marker lane (see above): the lane's own
              // y, plus a reviewed marker's radius at this zoom, plus a gap.
              // While the report lane is shown they hang under that one, so
              // they cover neither lane.
              const laneY = u.valToPos(reportLaneActive ? REPORT_LANE_Y : MARKER_LANE_Y, MARKER_LANE_SCALE, true)
              const tagH = FLAG_TAG_HEIGHT_PX * dpr
              const tagTop = laneY + (9 * visibleMarkerScale(u) * dpr) / 2 + 3 * dpr
              const padX = FLAG_TAG_PAD_X_PX * dpr
              const gap = FLAG_TAG_GAP_PX * dpr
              const right = left + width
              const surface = cssVar('--color-surface', '#ffffff')
              ctx.font = `600 ${FLAG_TAG_FONT_PX * dpr}px ${cssVar('--font-sans', 'system-ui, sans-serif')}`
              ctx.textAlign = 'left'
              ctx.textBaseline = 'middle'

              // Tags repeat the same few strings ("FP 0.912", "FN"); measure
              // each distinct one once per draw.
              const widths = new Map<string, number>()
              const textWidth = (text: string): number => {
                let w = widths.get(text)
                if (w === undefined) {
                  w = ctx.measureText(text).width
                  widths.set(text, w)
                }
                return w
              }

              // The current flag's tag is drawn last so it is never hidden
              // by a neighbour's.
              for (const currentPass of [false, true]) {
                if (!currentPass && !showAllTags) continue
                visible.forEach(({ flag, x, current }, i) => {
                  if (current !== currentPass) return
                  const color = colorOf(flag.kind)
                  const kindLabel = FLAG_KIND_LABEL[flag.kind]
                  const score = formatPrediction(flag.prediction)
                  // A tag has to end before the next flag's line (or the plot
                  // edge); the current one is always drawn, over its neighbours.
                  const limit = current || i + 1 >= visible.length ? right : visible[i + 1].x - gap
                  const tagLeft = x + (current ? 3 : 2) * dpr
                  let text = score ? `${kindLabel} ${score}` : kindLabel
                  let tagW = textWidth(text) + padX * 2
                  if (tagLeft + tagW > limit) {
                    text = kindLabel
                    tagW = textWidth(text) + padX * 2
                  }
                  let tagX = tagLeft
                  if (tagLeft + tagW > limit) {
                    // Not even the short form fits to the right: only the
                    // current flag is worth squeezing in, to the line's left.
                    if (!current) return
                    tagX = x - 3 * dpr - tagW
                  }

                  ctx.beginPath()
                  if (typeof ctx.roundRect === 'function') ctx.roundRect(tagX, tagTop, tagW, tagH, 2 * dpr)
                  else ctx.rect(tagX, tagTop, tagW, tagH)
                  if (current) {
                    ctx.fillStyle = color
                    ctx.fill()
                  } else {
                    ctx.globalAlpha = 0.92
                    ctx.fillStyle = surface
                    ctx.fill()
                    ctx.globalAlpha = 1
                  }
                  ctx.lineWidth = dpr
                  ctx.strokeStyle = color
                  ctx.stroke()
                  ctx.fillStyle = current ? readableTextOn(color) : color
                  ctx.fillText(text, tagX + padX, tagTop + tagH / 2 + dpr * 0.5)
                })
              }
            } finally {
              ctx.restore()
            }
          } catch {
            // ignore — see above
          }
        },
      },
    }
  }

  // The flag drawing reads `flaggedTimes` and `currentFlagTs` at draw time, so
  // a change to either has to ask for a repaint explicitly.
  $effect(() => {
    flaggedTimes
    currentFlagTs
    chart?.redraw()
  })

  // While bad-data mode is active, a left-drag on the plot selects a range
  // to mark instead of panning — the two behaviors are mutually exclusive
  // per drag, gated by `badDataMode` at drag-start. Uses the same
  // `endActiveDrag` teardown slot as the pan drag below (see its own
  // discipline note), so onMount/onDestroy cleanup tears this drag down too
  // if the component unmounts mid-drag.
  function handleBadDataDragStart(u: uPlot, downEvent: MouseEvent) {
    const rect = u.over.getBoundingClientRect()
    let startVal = u.posToVal(downEvent.clientX - rect.left, 'x')
    const startPx = downEvent.clientX

    // Pressing on an edge of the selected range grabs that edge: the far
    // edge stays fixed and the drag decides the new position of this one.
    let resizing: BadDataMark | null = null
    if (selectedMark) {
      const pressX = downEvent.clientX - rect.left
      const nearStart = Math.abs(u.valToPos(selectedMark.start, 'x') - pressX) <= MARK_EDGE_HIT_PX
      const nearStop = Math.abs(u.valToPos(selectedMark.stop, 'x') - pressX) <= MARK_EDGE_HIT_PX
      if (nearStart || nearStop) {
        resizing = selectedMark
        startVal = nearStart && !(nearStop && selectedMark.start === selectedMark.stop)
          ? selectedMark.stop
          : selectedMark.start
      }
    }

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

      // A click rather than a drag: in this mode that means "select the mark
      // I clicked on" (so it can be resized, edited or deleted), or clear the
      // selection when the click lands on empty trace, instead of silently
      // creating a zero-width mark.
      if (movedPx < CLICK_DRAG_THRESHOLD_PX) {
        if (!resizing) selectMark(markAt(u.posToVal(upEvent.clientX - rect.left, 'x')) ?? null)
        return
      }

      const endVal = u.posToVal(upEvent.clientX - rect.left, 'x')
      if (resizing) {
        void updateBadDataMark(resizing, startVal, endVal)
      } else {
        void submitBadDataMark(startVal, endVal)
      }
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
    if (beat) {
      noteManualSelection()
      onBeatSelect(beat)
    }
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
        if (addBeatMode) void handleAddBeatClick(u, upEvent)
        else handleBeatClick(u, upEvent)
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
      yManuallyZoomed = true
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
    yManuallyZoomed = true
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

  // Bottom X-range slider: same live-feedback-then-settle shape as
  // `handleDragStart`'s mouse pan (immediate `setScale` + `maybeBufferAhead`
  // on every move, a real fetch only once the gesture ends), so dragging the
  // slider feels the same as dragging the trace itself and a fast drag
  // doesn't spam the backend. The slider's value is the window's CENTER
  // (not its start) and its own width is held fixed at the current zoom
  // level — see the `min`/`max`/`value` bindings in the template, which
  // derive from `fullExtent` and `sliderHalfWidth`.
  function handleXSliderInput(e: Event) {
    if (!chart) return
    const center = Number((e.currentTarget as HTMLInputElement).value)
    const newMin = center - sliderHalfWidth
    const newMax = center + sliderHalfWidth
    viewChanged = true
    chart.setScale('x', { min: newMin, max: newMax })
    maybeBufferAhead(newMin, newMax)
  }

  function handleXSliderChange() {
    if (!chart) return
    const { min, max } = chart.scales.x
    if (min != null && max != null) scheduleRefetch(min, max)
  }

  // Brings `ts` into view with the beats BEFORE it (see `focusRange`),
  // keeping the current zoom level if the technician has already zoomed in
  // tighter than the default focus window.
  function focusOnTime(ts: number): Promise<void> {
    if (!chart) return Promise.resolve()
    const { min, max } = chart.scales.x
    const focusWidth = focusWidthSeconds(typicalRr)
    const currentWidth = min != null && max != null ? max - min : focusWidth
    const { start, end } = focusRange(ts, Math.min(currentWidth, focusWidth))
    return setVisibleRange(start, end)
  }

  // Jumping to a beat of interest also SELECTS it, so the detail panel shows
  // that beat's categories and the confirm/reject controls act on it — the
  // review loop the old app's next/prev-arrhythmia buttons drove.
  async function goToBeatOfInterest(index: number) {
    if (beatsOfInterest.length === 0) return
    const clamped = Math.max(0, Math.min(beatsOfInterest.length - 1, index))
    beatOfInterestIndex = clamped
    const ts = beatsOfInterest[clamped]
    // The technician's own choice: it replaces a flag selection still waiting
    // for detection, and supersedes a flag jump still waiting on its fetch.
    noteManualSelection()
    const epoch = selectionEpoch

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
      // Gone, or superseded by a newer jump or a beat chosen meanwhile?
      if (mountCancelled || epoch !== selectionEpoch) return
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

  // The flagged beat's full record, once the graph holds it — beat detection
  // may still be queued (nothing to find), or the beat may be outside the
  // loaded window until the move below has fetched it. The closest beat within
  // tolerance wins, so two flags a millisecond apart each get their own beat.
  function findFlaggedBeat(ts: number): WindowBeat | undefined {
    let nearest: WindowBeat | undefined
    let nearestDist = FLAG_BEAT_TOLERANCE_SECONDS
    for (const beat of lastBeats) {
      const dist = Math.abs(beat.ts - ts)
      if (dist < nearestDist) {
        nearest = beat
        nearestDist = dist
        if (dist === 0) break
      }
    }
    return nearest
  }

  // Position of the flag at `ts` in `flags`. Exact first: `currentFlagTs` is
  // normally one of the list's own timestamps, and two flags closer together
  // than the tolerance must stay distinct, or the navigation would resolve the
  // second back to the first and never get past it. The tolerance only
  // covers a `ts` that came from somewhere else.
  function indexOfFlagTime(flags: FlaggedTime[], ts: number | null): number {
    if (ts === null) return -1
    const exact = flags.findIndex((flag) => flag.ts === ts)
    if (exact >= 0) return exact
    let nearest = -1
    let nearestDist = FLAG_BEAT_TOLERANCE_SECONDS
    flags.forEach((flag, i) => {
      const dist = Math.abs(flag.ts - ts)
      if (dist < nearestDist) {
        nearest = i
        nearestDist = dist
      }
    })
    return nearest
  }

  function isInView(ts: number): boolean {
    const { min, max } = chart?.scales.x ?? {}
    return min != null && max != null && ts >= min && ts <= max
  }

  // A beat the technician chose themselves (a marker click, a beat of
  // interest, an added beat). Whatever flag selection was still waiting for
  // detection to deliver its beat is dropped, and a jump still waiting on its
  // fetch is superseded: neither may override this choice later.
  function noteManualSelection() {
    selectionEpoch += 1
    pendingFlagSelectTs = null
    pendingReportSelectTs = null
  }

  // Reconciles the current flag with the beats the graph holds right now, and
  // returns whether the flag's own beat is among them. Called when a jump
  // starts and every time freshly fetched beats are committed:
  //  - if a selection is still owed (`pendingFlagSelectTs`) and the beat is
  //    here, it is selected — the case of detection finishing AFTER the
  //    recording was opened on a flag. A jump's own first call passes
  //    `inView: false` (the view is about to move to the flag); every later
  //    one requires the flag to be on screen, so a technician who has panned
  //    away while detection ran does not get an off-screen beat selected;
  //  - if the beat is absent, `flagBeatMissing` is set, but only when the
  //    beats just loaded actually cover the flag's time (a flag scrolled
  //    outside the loaded window says nothing either way).
  function settleFlagBeat(requireInView: boolean = true): boolean {
    const ts = currentFlagTs
    // No flags any more (the project was closed): nothing is owed.
    if (flaggedTimes.length === 0) {
      pendingFlagSelectTs = null
      flagBeatMissing = false
      return false
    }
    if (ts === null) return false
    const beat = findFlaggedBeat(ts)
    if (beat) {
      flagBeatMissing = false
      if (pendingFlagSelectTs !== null && isSameFlagTime(pendingFlagSelectTs, ts) && (!requireInView || isInView(ts))) {
        pendingFlagSelectTs = null
        onBeatSelect?.(beat)
      }
      return true
    }
    if (lastBeatsRange && ts >= lastBeatsRange.start && ts <= lastBeatsRange.end) flagBeatMissing = true
    return false
  }

  // Brings the flag at `ts` into view — centred, with the beats either side
  // (see `flagFocusRange`) — makes it the current flag, and selects its beat
  // so the detail panel and the confirm/reject controls act on it, the same
  // way `goToBeatOfInterest` does. `keepZoom` holds a tighter zoom the
  // technician already chose, like `focusOnTime`; the opening view of a
  // recording ignores the zoom it happens to be at.
  //
  // A flag whose beat is NOT found (another channel or beat settings, a
  // deleted beat, detection not done yet) must not leave the previous
  // selection active while the view moves away from it — C / R would act on
  // an off-screen beat — so the selection is cleared instead, and the flag's
  // beat is selected later if detection delivers it (`settleFlagBeat`).
  async function focusOnFlag(ts: number, keepZoom: boolean): Promise<void> {
    if (!chart) return
    const epoch = ++selectionEpoch
    currentFlagTs = ts
    pendingFlagSelectTs = ts
    // A flag jump replaces a report jump still waiting for its beat.
    pendingReportSelectTs = null
    flagBeatMissing = false

    // Select immediately when the beat is already loaded, so the detail
    // panel responds without waiting on the network; otherwise drop the
    // old selection right away.
    const loaded = settleFlagBeat(false)
    if (!loaded) onBeatClear?.()

    const { min, max } = chart.scales.x
    const maxWidth = keepZoom && min != null && max != null ? max - min : null
    const { start, end } = flagFocusRange(ts, typicalRr, fullExtent, maxWidth)
    const pending = setVisibleRange(start, end)

    // Outside the loaded buffer (a long recording), the beat only arrives
    // with this fetch. If detection hasn't produced beats yet there is still
    // nothing to select afterwards: the selection stays cleared, the highlight
    // on the trace stays, and the beat is picked up when detection finishes.
    if (!loaded) {
      await pending
      // Gone (the graph was unmounted), or superseded by a newer jump or a
      // beat the technician chose while the fetch was in flight?
      if (mountCancelled || !chart || epoch !== selectionEpoch) return
      if (!settleFlagBeat()) onBeatClear?.()
    }
  }

  function goToFlag(index: number) {
    if (!chart || flaggedTimes.length === 0) return
    const clamped = Math.max(0, Math.min(flaggedTimes.length - 1, index))
    const ts = flaggedTimes[clamped].ts
    // Reported straight away (not after the fetch) so the sidebar's
    // highlight follows the click.
    onFlagNavigate?.(ts)
    // The report makes the parent's `focusRequest` carry this flag with a
    // `seq` this graph has not seen (it was mounted without one). Reading the
    // prop recomputes it, so that `seq` can be recorded as handled now —
    // otherwise the effect below would take it for a new request and focus
    // the flag a second time. A later request (a sidebar click: new `seq`)
    // still moves the graph.
    const echoed = focusRequest
    if (echoed) handledFocusSeq = echoed.seq
    void focusOnFlag(ts, true)
  }

  function handlePrevFlag() {
    // From "no current flag yet", stepping back lands on the last one.
    goToFlag(flagIndex <= 0 ? flaggedTimes.length - 1 : flagIndex - 1)
  }

  function handleNextFlag() {
    goToFlag(flagIndex + 1 >= flaggedTimes.length ? 0 : flagIndex + 1)
  }

  // A request that arrives while the graph is already showing the recording
  // (another flag clicked in the sidebar). One that arrives before the chart
  // exists is picked up by `buildChart` instead; only a `seq` not yet handled
  // counts, so `onFlagNavigate`'s own report (same `seq`, new `ts`) never
  // moves the graph.
  $effect(() => {
    const request = focusRequest
    if (!request || !chartReady) return
    if (request.seq === handledFocusSeq) return
    handledFocusSeq = request.seq
    const ts = request.ts
    untrack(() => void focusOnFlag(ts, true))
  })

  // ---- Report navigator ------------------------------------------------------
  // Steps through the report's beats for the chosen filter. A jump centres the
  // view on the report beat (`flagFocusRange`, keeping a tighter zoom the
  // technician already chose) and selects OUR beat matched to it, so the beat
  // panel and C / R act on the beat the report is talking about. A report beat
  // with no beat of ours (not detected here) selects nothing — and clears the
  // previous selection, so C / R can never act on a beat the view has left.

  // What the plugin draws, read at every draw.
  function reportLaneState(): ReportLaneState {
    return {
      active: reportLaneActive,
      beats: lastReportBeats,
      showNormal: showReportNormal,
      badData: reportBadData ?? [],
      currentTs: currentReportTs,
    }
  }

  // Selects our beat matched to the report beat `rb`, if the graph holds it and
  // a selection is owed (`pendingReportSelectTs`); returns whether that beat
  // is held. A report beat that cannot have a beat of ours (`match: 'none'`)
  // owes nothing; one whose beats are not detected yet keeps the selection
  // pending until they are.
  function selectMatchedBeat(rb: ReportBeat | undefined, requireInView: boolean): boolean {
    if (!rb) return false
    if (rb.match === 'none') {
      reportBeatUnmatched = true
      pendingReportSelectTs = null
      return false
    }
    if (rb.our_ts == null) return false
    reportBeatUnmatched = false
    const ours = findFlaggedBeat(rb.our_ts)
    if (!ours) return false
    if (
      pendingReportSelectTs !== null &&
      currentReportTs !== null &&
      pendingReportSelectTs === currentReportTs &&
      (!requireInView || isInView(rb.ts))
    ) {
      pendingReportSelectTs = null
      onBeatSelect?.(ours)
    }
    return true
  }

  // `settleFlagBeat`'s counterpart: reconciles the current report beat with the
  // report beats and beats the graph holds right now. Called when a jump starts
  // and every time freshly fetched data is committed, so a beat that only
  // arrives later (detection finishing, the jump's own fetch) is still
  // selected — provided the report beat is on screen by then.
  function settleReportBeat(requireInView: boolean = true): boolean {
    const ts = currentReportTs
    if (!reportLoaded) {
      pendingReportSelectTs = null
      return false
    }
    if (ts === null) return false
    return selectMatchedBeat(findReportBeat(lastReportBeats, ts), requireInView)
  }

  // The report's entry for a target the loaded window does not contain (a
  // normal-labelled beat while those are not fetched).
  async function lookupReportBeat(ts: number): Promise<ReportBeat | undefined> {
    try {
      const result = await getReportBeat(path, ts)
      const beat = result?.status === 'ok' && !result.error ? result.beat : null
      if (beat && Math.abs(beat.ts - ts) < REPORT_TARGET_TOLERANCE_SECONDS) return beat
    } catch {
      // no entry: the selection stays cleared
    }
    return undefined
  }

  async function focusOnReportBeat(ts: number): Promise<void> {
    if (!chart) return
    const epoch = ++selectionEpoch
    // A report jump replaces a flag jump still waiting for its beat.
    pendingFlagSelectTs = null
    currentReportTs = ts
    pendingReportSelectTs = ts
    reportBeatUnmatched = false

    // Select immediately when the beat is already held, so the detail panel
    // responds without waiting on the network; otherwise drop the old
    // selection right away.
    const settled = settleReportBeat(false)
    if (!settled) onBeatClear?.()

    const { min, max } = chart.scales.x
    const maxWidth = min != null && max != null ? max - min : null
    const { start, end } = flagFocusRange(ts, typicalRr, fullExtent, maxWidth)
    const pending = setVisibleRange(start, end)
    if (settled) return

    // Outside the loaded buffer the beats only arrive with this fetch. Gone (the
    // graph was unmounted), or superseded by a newer jump or a beat the
    // technician chose while it was in flight?
    await pending
    if (mountCancelled || !chart || epoch !== selectionEpoch) return
    if (settleReportBeat() || reportBeatUnmatched) return
    if (findReportBeat(lastReportBeats, ts)) return // known, but our beat is not detected/loaded yet
    const entry = await lookupReportBeat(ts)
    if (mountCancelled || !chart || epoch !== selectionEpoch) return
    selectMatchedBeat(entry, true)
  }

  function goToReportTarget(index: number) {
    if (!chart || index < 0 || index >= reportFilterTargets.length) return
    const ts = reportFilterTargets[index]
    // Reported straight away (not after the fetch), like a flag jump.
    onReportNavigate?.(ts)
    void focusOnReportBeat(ts)
  }

  function handlePrevReport() {
    goToReportTarget(stepTarget(reportFilterTargets, currentReportTs, -1))
  }

  function handleNextReport() {
    goToReportTarget(stepTarget(reportFilterTargets, currentReportTs, 1))
  }

  // Dropping the navigator's place: another filter was chosen, or the report
  // went away. Supersedes a jump still waiting on its fetch.
  function resetReportNavigation() {
    selectionEpoch += 1
    pendingReportSelectTs = null
    currentReportTs = null
    reportBeatUnmatched = false
  }

  function handleReportFilterChange(event: Event) {
    const select = event.currentTarget as HTMLSelectElement
    const value = select.value as ReportFilter
    if (!REPORT_FILTERS.includes(value)) return
    reportFilterChoice = value
    storeString(REPORT_FILTER_STORAGE_KEY, value)
    resetReportNavigation()
    // A focused <select> would swallow `,` / `.` (see `shouldIgnoreShortcut`).
    select.blur()
  }

  function toggleReportLane() {
    showReportLane = !showReportLane
    storeString(REPORT_LANE_STORAGE_KEY, showReportLane ? '1' : '0')
  }

  function toggleReportNormal() {
    showReportNormal = !showReportNormal
    storeString(REPORT_NORMAL_STORAGE_KEY, showReportNormal ? '1' : '0')
  }

  // The second lane needs room above the trace (see the y scale's `range`), which
  // uPlot only works out when it re-ranges.
  function rerangeForReportLane() {
    if (!chart) return
    // A refetch on its way re-ranges when it lands, against the new headroom;
    // a y scale the technician zoomed by hand is theirs to keep.
    if (channelFetchInFlight > 0 || yManuallyZoomed) {
      chart.redraw()
      return
    }
    const { min, max } = chart.scales.x
    chart.setData(chart.data, true)
    if (min != null && max != null) chart.setScale('x', { min, max })
    applyFilters()
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
        // Fires on every x-scale change regardless of source (mouse drag,
        // wheel, zoom buttons, page/jump/focus, the slider itself) — one
        // place to keep the slider's thumb position/width correct instead
        // of threading a sync call through every one of those call sites.
        setScale: (u, key) => {
          if (key !== 'x') return
          const { min, max } = u.scales.x
          if (min == null || max == null) return
          sliderCenter = (min + max) / 2
          sliderHalfWidth = (max - min) / 2
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
            const isHovered = mark.id === hoveredMarkId || mark.id === selectedMarkId
            paintRegion(
              u,
              mark.start,
              mark.stop,
              isHovered ? cssVar('--color-bad-data-fill-hover', 'rgba(185, 28, 28, 0.28)') : markFill,
              mark.id === selectedMarkId ? cssVar('--color-danger', '#b91c1c') : markStroke,
            )
            if (mark.id === selectedMarkId) {
              // Edge handles: thick bars showing where a drag will resize.
              ctx.fillStyle = cssVar('--color-danger', '#b91c1c')
              for (const edge of [mark.start, mark.stop]) {
                const x = u.valToPos(edge, 'x', true)
                ctx.fillRect(x - 2, u.bbox.top, 4, u.bbox.height)
              }
            }
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

  // The mount load, pulled out of `onMount` so it can be retried. A first
  // load that fails used to leave the chart permanently unbuilt — no
  // waveform, no markers, and no way back short of switching channels —
  // which is the other half of the "graph stays empty" report: the backend
  // is spawned asynchronously by Electron, so the very first request after
  // opening a file can genuinely arrive too early.
  async function initialLoad(): Promise<boolean> {
    if (!containerEl) return false
    loading = true
    try {
      const result = await fetchMergedWindow(0, Number.MAX_SAFE_INTEGER, measuredWidth())
      if (mountCancelled || !containerEl) return false
      if (!result) return false
      buildChart(result)
      return true
    } finally {
      if (!mountCancelled) loading = false
    }
  }

  let mountCancelled = false

  onMount(() => {
    mountCancelled = false

    void (async () => {
      if (await initialLoad()) return
      if (mountCancelled) return
      // One automatic retry covers the common transient case (the backend
      // still coming up) without the technician having to do anything; the
      // Retry button below covers everything else.
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS))
      if (mountCancelled) return
      void initialLoad()
    })()

    return () => {
      mountCancelled = true
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

  function buildChart(result: {
    channel: { x: number[]; y: number[] }
    beats: WindowBeat[]
    merged: BeatSeriesData
    report: ReportFetch
  }): void {
      const width = measuredWidth()
      const { channel: data, beats, merged, report } = result
      // `fetchMergedWindow` deliberately leaves these to its caller (see its
      // own comment), so the mount path has to commit them too — they are
      // what click hit-testing and every later local re-merge read from.
      lastChannelWindow = data
      lastBeats = beats
      commitReportFetch(report)
      // The mount fetch asks for the whole file (see `initialLoad`).
      lastBeatsRange = { start: -Infinity, end: Infinity }

      fullExtent = { start: data.x[0] ?? 0, end: data.x[data.x.length - 1] ?? 0 }
      // The initial mount fetch already loads the whole file's extent (see
      // the `0, Number.MAX_SAFE_INTEGER` call above) — seeding `loadedRange`
      // from it means a subsequent zoom-in is immediately recognized as
      // needing a real fetch (via `needsRefetch`'s resolution check) with
      // no separate null-case special-casing needed.
      loadedRange = {
        start: fullExtent.start,
        end: fullExtent.end,
        requestedWidth: fullExtent.end - fullExtent.start,
        pixelWidth: width,
      }

      chart = new uPlot(
        {
          width,
          height: graphHeight(),
          // `y.auto: true` is uPlot's own default (implicit until now) —
          // stated explicitly since `handleResetView` toggles it back on
          // after a manual Y-zoom, and Y-zoom (`zoomYBy`/Shift+wheel) turns
          // it off implicitly the same way X's own manual `setScale` always
          // has.
          scales: {
            x: { time: false },
            // Leaves headroom above the trace for the marker lane (below),
            // and for the report lane under it while one is shown.
            y: {
              auto: true,
              range: (_u, dataMin, dataMax) => {
                const span = dataMax - dataMin || 1
                const headroom = reportLaneActive ? REPORT_LANE_HEADROOM : MARKER_LANE_HEADROOM
                return [dataMin - span * 0.05, dataMax + span * headroom]
              },
            },
            [MARKER_LANE_SCALE]: { auto: false, range: [0, 1] },
          },
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
            // The marker lane has no visible axis; declaring it (hidden)
            // just tells uPlot the scale runs vertically.
            { scale: MARKER_LANE_SCALE, show: false, side: 1 },
          ],
          series: [
            {},
            { stroke: () => waveformColor(), width: 1.5, spanGaps: true },
            ...MARKER_BUCKETS.map((bucket) => categoryMarkerSeries(bucket.category, bucket.reviewed)),
            selectionHighlightSeries(),
            rejectedOverlaySeries(),
          ],
          plugins: [
            panZoomPlugin(),
            badDataPlugin(),
            flagPlugin(),
            reportLanePlugin(reportLaneState, {
              cssVar,
              laneScale: MARKER_LANE_SCALE,
              ourLaneY: MARKER_LANE_Y,
              markerScale: visibleMarkerScale,
            }),
            overlayAnchorPlugin(),
            pendingBeatPlugin(),
          ],
        },
        toChartData(merged),
        containerEl,
      )
      // Seeds the slider immediately rather than waiting on the `setScale`
      // hook to have fired by now — belt-and-braces, since it's cheap and
      // means the slider never briefly shows a stale/zeroed range.
      sliderCenter = (fullExtent.start + fullExtent.end) / 2
      sliderHalfWidth = (fullExtent.end - fullExtent.start) / 2
      applyFilters()
      chartReady = true

      // The report changed (loaded, removed, replaced, our beats edited) while
      // the first load was in flight, so the report beats it brought are out
      // of date: ask again for what is now shown. The same when the first
      // (whole-file) window had to leave the normal beats out but the file is
      // short enough to have them.
      if (reportNeedsRefetch(report)) void refreshReportOnly()

      // FP/FN review: a recording opened for a flag (the sidebar, or the
      // Home screen) opens zoomed on it rather than on the start of the file.
      // Recorded as handled so the `focusRequest` effect below doesn't repeat
      // it now that the chart exists.
      const request = focusRequest
      if (request) {
        handledFocusSeq = request.seq
        void focusOnFlag(request.ts, false)
        return
      }

      // Opens on the first few seconds of the file (Settings → Appearance)
      // rather than the whole recording, and never jumps to a beat of
      // interest on its own — the technician steps to those with N/P.
      const openWidth = graphPrefs.initialWindowSeconds
      if (fullExtent.end - fullExtent.start > openWidth) {
        void setVisibleRange(fullExtent.start, fullExtent.start + openWidth)
      }
  }

  // Reacts to `bind:clientWidth` changes on the container (window resizes,
  // layout settling, sidebar collapse, entering focus mode).
  $effect(() => {
    containerWidth // the binding is the change signal; the live measurement below is the truth
    if (!chart) return

    // Compared against the chart's OWN width rather than a remembered
    // previous value: the chart can be constructed at the fallback width
    // (container not yet laid out), and a "skip the first observation"
    // guard would then never correct it, leaving the canvas wider than its
    // container for the rest of the session.
    const width = measuredWidth()
    if (width === chart.width) return

    chart.setSize({ width, height: graphHeight() })
    // Point density is tied to pixel width, so a wider graph needs a
    // denser fetch — `needsRefetch` checks exactly that.
    const { min, max } = chart.scales.x
    if (min != null && max != null) scheduleRefetch(min, max)
  })

  // Keeps the live chart sized to its container and the current mode.
  // Guarded on `chart` existing rather than coordinating with the async
  // mount setup.
  $effect(() => {
    // Re-read every input to graphHeight() so this re-runs for any of them.
    fullscreen
    viewportHeight
    if (!chart) return
    chart.setSize({ width: measuredWidth(), height: graphHeight() })
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

  // Mirrors the `containerWidth` effect above: the binding
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

  // Report labels: re-fetches the report window — and only that — when what
  // it depends on changes: the parent's `reportRefreshToken` (the report was
  // loaded, replaced or removed, or our own beats changed), `reportLoaded`
  // itself, or the "show normal beats" switch (which changes what the
  // backend is asked for). One effect for all three, so changes arriving
  // together cost one request. The first run only records the starting values:
  // the initial fetch in `onMount` already asked for the report. The work is
  // `untrack`ed, so nothing it reads or writes can retrigger this effect.
  $effect(() => {
    const inputs = { loaded: reportLoaded, token: reportRefreshToken, normal: showReportNormal }
    const previous = previousReportInputs
    previousReportInputs = inputs
    if (!previous) return
    if (previous.loaded === inputs.loaded && previous.token === inputs.token && previous.normal === inputs.normal) return
    untrack(() => {
      reportEpoch += 1
      if (!inputs.loaded) resetReportNavigation()
      void refreshReportOnly()
    })
  })

  // The lane, the disagreement bands, the hatching and the current beat's ring
  // are drawn from state the plugin reads at draw time, so a change to any of
  // it has to ask for a repaint explicitly.
  $effect(() => {
    reportLoaded
    showReportLane
    showReportNormal
    reportBadData
    currentReportTs
    untrack(() => chart?.redraw())
  })

  // The second lane takes room off the top of the plot, so the y scale has to
  // be re-ranged when it appears or goes away (a fetch landing re-ranges by
  // itself; this is for the switch). The first run only records the value —
  // the chart is built against whatever it already is.
  $effect(() => {
    const active = reportLaneActive
    if (previousReportLaneActive === undefined) {
      previousReportLaneActive = active
      return
    }
    if (active === previousReportLaneActive) return
    previousReportLaneActive = active
    untrack(() => rerangeForReportLane())
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
        if (showFocusOverlay) {
          overlayDismissedFor = selectedBeatTs
        } else if (selectedMarkId !== null) {
          selectMark(null)
        } else if (pendingBeat) {
          pendingBeat = null
          addBeatError = null
        } else if (addBeatMode) {
          leaveAddBeatMode()
        } else if (badDataMode) {
          badDataMode = false
          hoveredMarkId = null
        } else if (fullscreen) {
          fullscreen = false
          toolsCollapsed = false
        }
        return
      }

      // `[` and `]` are typed with AltGr (Windows, reported as Ctrl+Alt) or
      // Option (macOS) on layouts such as German, which the shared guard
      // would treat as a modifier combination and drop. For those two keys
      // the produced character is what matters, so with Alt held the guard is
      // asked about the keystroke without modifiers (it still checks for a
      // text field or an open dialog). Ctrl+[ and Cmd+[ stay ignored.
      const isBracket = event.key === '[' || event.key === ']'
      const typedWithAlt = isBracket && event.altKey && !event.metaKey
      if (
        shouldIgnoreShortcut(
          typedWithAlt
            ? ({ metaKey: false, ctrlKey: false, altKey: false, target: event.target } as unknown as KeyboardEvent)
            : event,
        )
      ) {
        return
      }

      // Walking hundreds of flagged beats is the core review loop, so it
      // shouldn't require moving the mouse between the graph and the panel
      // for every single one.
      switch (event.key) {
        case ']':
          if (flaggedTimes.length === 0) return
          event.preventDefault()
          handleNextFlag()
          break
        case '[':
          if (flaggedTimes.length === 0) return
          event.preventDefault()
          handlePrevFlag()
          break
        // The report navigator: `,` and `.` are the unshifted "<" and ">" of
        // most layouts, so they read as previous / next.
        case ',':
          if (!reportLoaded || reportFilterTargets.length === 0) return
          event.preventDefault()
          handlePrevReport()
          break
        case '.':
          if (!reportLoaded || reportFilterTargets.length === 0) return
          event.preventDefault()
          handleNextReport()
          break
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
        case 'Delete':
          if (selectedMark) {
            event.preventDefault()
            void removeBadDataMark(selectedMark)
          }
          break
        case 'b':
        case 'B':
          event.preventDefault()
          toggleBadDataMode()
          break
        case 'a':
        case 'A':
          event.preventDefault()
          toggleAddBeatMode()
          break
        case 'Enter':
          if (pendingBeat) {
            event.preventDefault()
            void confirmAddBeat()
          }
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
    selectedMarkId
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

  function handleResetView() {
    if (!chart || !fullExtent) return
    if (debounceTimer !== undefined) {
      clearTimeout(debounceTimer)
      debounceTimer = undefined
    }
    viewChanged = false
    chart.setScale('x', { min: fullExtent.start, max: fullExtent.end })
    // Reset View also hands Y back to auto-ranging, undoing any manual
    // Y-zoom — see `refetch`'s own comment on how.
    yManuallyZoomed = false
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
    </div>

    <!-- FP/FN review: only for a recording some project flagged beats in.
         Tinted with the current flag's kind color so it reads as a separate
         tool from the beat-of-interest walk next to it. -->
    {#if flaggedTimes.length > 0}
      <div
        class="beat-nav-controls flag-nav"
        data-testid="flag-nav"
        role="group"
        aria-label="Model flags"
        style={`--flag-nav-color: var(${FLAG_KIND_COLOR_VAR[currentFlag?.kind ?? 'flag']}); --flag-nav-soft: var(${FLAG_KIND_SOFT_VAR[currentFlag?.kind ?? 'flag']})`}
      >
        <span class="flag-nav-label" title="Beats the model got wrong, from the imported FP/FN list">
          <Icon name="target" size={14} />
          Model flags
        </span>
        <button
          type="button"
          class="btn btn-sm"
          data-testid="flag-prev-button"
          title="Previous model flag ([)"
          onclick={handlePrevFlag}
        >
          <Icon name="chevron-left" size={14} />
        </button>
        <span
          class="flag-nav-counter"
          data-testid="flag-nav-counter"
          title={currentFlag ? FLAG_KIND_TITLE[currentFlag.kind] : undefined}
        >
          {#if currentFlag}
            {@const score = formatPrediction(currentFlag.prediction)}
            {FLAG_KIND_LABEL[currentFlag.kind]} {flagIndex + 1} of {flaggedTimes.length} · {formatFlagTime(currentFlag.ts)}{score ? ` · ${score}` : ''}
            {#if flagBeatMissing}
              <span class="flag-nav-missing" data-testid="flag-no-beat" title={FLAG_NO_BEAT_TITLE}>· no detected beat here</span>
            {/if}
          {:else}
            {flaggedTimes.length} flagged {flaggedTimes.length === 1 ? 'beat' : 'beats'}
          {/if}
        </span>
        <button
          type="button"
          class="btn btn-sm"
          data-testid="flag-next-button"
          title="Next model flag (])"
          onclick={handleNextFlag}
        >
          <Icon name="chevron-right" size={14} />
        </button>
      </div>
    {/if}

    <!-- Report labels: only for a recording an xlsx report is attached to.
         Steps through the report's own beats (by filter) and selects OUR beat
         matched to each, so the report can be compared with this review. -->
    {#if reportLoaded}
      <div
        class="beat-nav-controls report-nav"
        data-testid="report-nav"
        role="group"
        aria-label="Report labels"
      >
        <span class="report-nav-label" title="Beats labelled by the imported report">
          <Icon name="file-text" size={14} />
          Report
        </span>
        <select
          class="report-filter-select"
          data-testid="report-filter-select"
          aria-label="Report beats to step through"
          title={REPORT_FILTER_TITLE[reportFilter]}
          value={reportFilter}
          onchange={handleReportFilterChange}
        >
          {#each REPORT_FILTERS as filter (filter)}
            <option value={filter} title={REPORT_FILTER_TITLE[filter]} selected={filter === reportFilter}>
              {REPORT_FILTER_NAME[filter]} ({reportTargets?.[filter]?.length ?? 0})
            </option>
          {/each}
        </select>
        <button
          type="button"
          class="btn btn-sm"
          data-testid="report-prev-button"
          title="Previous report beat (,)"
          disabled={reportFilterTargets.length === 0}
          onclick={handlePrevReport}
        >
          <Icon name="chevron-left" size={14} />
        </button>
        <span class="report-nav-counter" data-testid="report-nav-counter">
          {#if reportFilterTargets.length === 0}
            None
          {:else if reportIndex < 0}
            {reportFilterTargets.length} {reportFilterTargets.length === 1 ? 'beat' : 'beats'}
          {:else}
            {reportIndex + 1} of {reportFilterTargets.length}
            {#if reportBeatUnmatched}
              <span class="report-nav-missing" data-testid="report-no-beat" title={REPORT_AGREEMENT_TEXT.unmatched}>
                · no beat here
              </span>
            {/if}
          {/if}
        </span>
        <button
          type="button"
          class="btn btn-sm"
          data-testid="report-next-button"
          title="Next report beat (.)"
          disabled={reportFilterTargets.length === 0}
          onclick={handleNextReport}
        >
          <Icon name="chevron-right" size={14} />
        </button>
      </div>
    {/if}

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

    <!-- Keyboard hints and the marker filter share one row: the filter used
         to sit in the legend under the plot, below the fold on most
         screens. It stays even with no beats of interest (only the hints
         depend on those). -->
    <div class="nav-hint-row">
      {#if beatsOfInterest.length > 0 || flaggedTimes.length > 0 || reportLoaded}
        <ul class="nav-hint" data-testid="beat-nav-hint" aria-label="Keyboard shortcuts">
          {#if beatsOfInterest.length > 0}
            <li><kbd>N</kbd> Next</li>
            <li><kbd>P</kbd> Previous</li>
          {/if}
          {#if flaggedTimes.length > 0}
            <li><kbd>[</kbd> <kbd>]</kbd> Model flags</li>
          {/if}
          {#if reportLoaded}
            <li data-testid="report-nav-hint"><kbd>,</kbd> <kbd>.</kbd> Report labels</li>
          {/if}
          {#if beatsOfInterest.length > 0}
            <li><kbd>C</kbd> Confirm arrhythmia</li>
            <li><kbd>R</kbd> Reject</li>
          {/if}
        </ul>
      {/if}
      <div class="ecg-legend-review-filter" role="radiogroup" aria-label="Filter by review status">
        <span
          class="legend-filter-label"
          data-testid="marker-filter-label"
          title="Hide or show beat markers by category, review status or rejected. This never changes the signal or the saved beats."
        >
          Show markers
        </span>
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

  <div class="ecg-graph-toolbar">
    <div class="trace-group" role="group" aria-label="Trace display">
      <span class="trace-group-label">Trace</span>
      <button
        type="button"
        class="btn btn-sm"
        class:btn-active={showFiltered}
        data-testid="toggle-filtered-signal-button"
        aria-pressed={showFiltered}
        title="Choose which waveform is drawn. Raw: the recorded signal. Filtered: the high-pass version that beat detection actually analyses (order and cutoff set in Settings). This only changes the display, not the detected beats."
        onclick={toggleFiltered}
      >
        <Icon name="filter" size={14} />
        {showFiltered ? 'Filtered' : 'Raw'}
      </button>
      <span class="trace-group-hint" data-testid="trace-filter-hint">
        {showFiltered ? 'High-pass, as used by beat detection' : 'As recorded'}
      </span>
    </div>
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
      class:btn-active={addBeatMode}
      data-testid="add-beat-mode-button"
      aria-pressed={addBeatMode}
      title={addBeatMode ? 'Leave add-beat mode (Esc)' : 'Add a beat the detector missed (A)'}
      onclick={toggleAddBeatMode}
    >
      <Icon name="plus" size={14} />
      {addBeatMode ? 'Exit add beat' : 'Add beat'}
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

  {#if addBeatMode}
    <div class="mode-banner" data-testid="add-beat-mode-banner">
      <Icon name="plus" size={14} />
      {#if pendingBeat}
        <span data-testid="add-beat-pending">
          <strong>New beat at {pendingBeat.ts.toFixed(3)} s</strong>
          ({pendingBeat.source === 'snapped' ? 'snapped to the nearest peak' : 'placed exactly where you clicked'}).
          Click elsewhere to move it, or confirm.
        </span>
        {#if pendingBeat.source === 'manual'}
          <button
            type="button"
            class="btn btn-sm"
            data-testid="add-beat-resnap-button"
            disabled={addBeatBusy}
            onclick={() => pendingBeat && snapPendingTo(pendingBeat.ts)}
          >
            Snap to peak
          </button>
        {/if}
        <button
          type="button"
          class="btn btn-sm btn-primary"
          data-testid="add-beat-confirm-button"
          disabled={addBeatBusy}
          onclick={confirmAddBeat}
        >
          Add beat <kbd>Enter</kbd>
        </button>
        <button
          type="button"
          class="btn btn-sm"
          data-testid="add-beat-cancel-button"
          onclick={() => {
            pendingBeat = null
            addBeatError = null
          }}
        >
          Cancel <kbd>Esc</kbd>
        </button>
      {:else}
        <span>
          <strong>Adding a beat.</strong> Click near the missing beat: it snaps to the nearest peak and the graph zooms in for you to check.
        </span>
        <button type="button" class="btn btn-sm" data-testid="exit-add-beat-mode-button" onclick={leaveAddBeatMode}>
          Done <kbd>Esc</kbd>
        </button>
      {/if}
      {#if addBeatError}
        <span class="text-danger" data-testid="add-beat-error">{addBeatError}</span>
      {/if}
    </div>
  {/if}

  {#if error}
    <div class="banner banner-error" data-testid="ecg-graph-error">
      <Icon name="alert-circle" size={14} />
      <span>{error}</span>
      {#if !chartReady}
        <button
          type="button"
          class="btn btn-sm"
          data-testid="retry-graph-load-button"
          disabled={loading}
          onclick={() => void initialLoad()}
        >
          {loading ? 'Retrying…' : 'Retry'}
        </button>
      {/if}
      <button type="button" class="btn btn-sm" data-testid="dismiss-graph-error" onclick={() => (error = null)}>
        Dismiss
      </button>
    </div>
  {/if}

  {#if loading && !chartReady}
    <div class="graph-placeholder" data-testid="ecg-graph-loading">
      <span class="spinner" aria-hidden="true"></span> Loading trace…
    </div>
  {/if}

  <div class="graph-stage">
  <div
    class="ecg-graph-container"
    class:marking={badDataMode || addBeatMode}
    class:over-mark={hoveredMarkId !== null}
    data-testid="ecg-graph-container"
    bind:this={containerEl}
    bind:clientWidth={containerWidth}
  ></div>

  {#if chartReady && fullExtent}
    {@const spanStart = fullExtent.start + sliderHalfWidth}
    {@const spanEnd = fullExtent.end - sliderHalfWidth}
    {@const sliderDisabled = spanStart >= spanEnd}
    {@const sliderMin = sliderDisabled ? (fullExtent.start + fullExtent.end) / 2 : spanStart}
    {@const sliderMax = sliderDisabled ? sliderMin : spanEnd}
    <!-- X-axis only: drag to move the visible window left/right along the
         recording. Stays in sync with the graph's own mouse pan/zoom via
         the `setScale` hook in `panZoomPlugin`, and vice versa. No Y slider
         — Y is amplitude, not a position to scroll through. -->
    <input
      type="range"
      class="x-range-slider"
      data-testid="x-range-slider"
      aria-label="Scroll graph horizontally"
      min={sliderMin}
      max={sliderMax}
      step="any"
      value={sliderCenter}
      disabled={sliderDisabled}
      oninput={handleXSliderInput}
      onchange={handleXSliderChange}
    />
  {/if}

  {#if showFocusOverlay && overlayPos}
    <div
      class="focus-overlay"
      data-testid="focus-beat-overlay"
      style={`left: ${overlayPos.left}px; top: ${overlayPos.top}px; width: ${OVERLAY_WIDTH_PX}px`}
    >
      <div class="focus-overlay-header">
        <span class="focus-overlay-title">Beat details</span>
        <button
          type="button"
          class="icon-btn"
          data-testid="focus-overlay-close"
          aria-label="Close beat details"
          title="Close (Esc)"
          onclick={() => (overlayDismissedFor = selectedBeatTs)}
        >
          <Icon name="x" size={14} />
        </button>
      </div>
      {@render focusOverlay?.()}
      <ul class="nav-hint focus-overlay-keys" aria-label="Keyboard shortcuts">
        <li><kbd>C</kbd> Confirm</li>
        <li><kbd>R</kbd> Reject</li>
        <li><kbd>N</kbd>/<kbd>P</kbd> Next / Previous</li>
        {#if flaggedTimes.length > 0}
          <li><kbd>[</kbd>/<kbd>]</kbd> Model flags</li>
        {/if}
        {#if reportLoaded}
          <li><kbd>,</kbd>/<kbd>.</kbd> Report labels</li>
        {/if}
      </ul>
    </div>
  {/if}
  </div>

  {#if badDataToast}
    <div class="graph-toast" data-testid="bad-data-toast" role="status">
      <Icon name="check-circle" size={14} />
      {badDataToast}
    </div>
  {/if}

  <!-- Whole-file overview of the marks: the shading on the chart itself
       (see badDataPlugin) covers the visible range, this covers everything
       off-screen too. Only shown once the file has marks — with none there
       is nothing to overview, and marking itself starts from the toolbar's
       "Mark bad data", not from this strip. -->
  {#if badDataMarks.length > 0}
    <div class="marks-strip-row">
      <span class="marks-strip-label">
        Bad data
        <span class="marks-count">{badDataMarks.length}</span>
      </span>
      <div data-testid="bad-data-marks-bar" class="bad-data-marks-bar">
        {#each badDataMarks as mark (mark.id)}
          <button
            type="button"
            data-testid="bad-data-mark"
            class="bad-data-mark"
            style={`left: ${markLeftPct(mark)}%; width: ${markWidthPct(mark)}%`}
            class:selected={mark.id === selectedMarkId}
            onclick={() => selectMark(mark)}
            title={`Bad data ${mark.start.toFixed(2)}–${mark.stop.toFixed(2)} (click to edit or delete)`}
            aria-label={`Bad data mark from ${mark.start.toFixed(2)} to ${mark.stop.toFixed(2)}, click to edit or delete`}
          ></button>
        {/each}
      </div>
    </div>
  {/if}

  {#if selectedMark}
    <div class="mark-editor" data-testid="bad-data-editor">
      <span class="mark-editor-title">Bad data range</span>
      <label>
        From
        <input
          type="text"
          inputmode="decimal"
          data-testid="bad-data-start-input"
          bind:value={editStart}
          onkeydown={(e) => e.key === 'Enter' && saveEditedRange()}
        />
      </label>
      <label>
        To
        <input
          type="text"
          inputmode="decimal"
          data-testid="bad-data-stop-input"
          bind:value={editStop}
          onkeydown={(e) => e.key === 'Enter' && saveEditedRange()}
        />
      </label>
      <span class="text-muted">s</span>
      <button
        type="button"
        class="btn btn-sm btn-primary"
        data-testid="bad-data-save-button"
        disabled={!editRangeValid}
        onclick={saveEditedRange}
      >
        Save range
      </button>
      <button
        type="button"
        class="btn btn-sm"
        data-testid="bad-data-delete-button"
        onclick={() => selectedMark && removeBadDataMark(selectedMark)}
      >
        <Icon name="trash" size={14} /> Delete
      </button>
      <button
        type="button"
        class="icon-btn"
        data-testid="bad-data-editor-close"
        aria-label="Close editor"
        onclick={() => selectMark(null)}
      >
        <Icon name="x" size={14} />
      </button>
      <span class="mark-editor-hint text-muted">
        {badDataMode ? 'Drag a red edge on the graph to resize.' : 'Turn on bad-data mode to drag its edges.'}
      </span>
    </div>
  {/if}

  <!-- Legend doubles as the marker filter: each entry toggles that
       category's visibility on the graph. -->
  <div class="ecg-legend" data-testid="ecg-legend">
    <div class="ecg-legend-categories">
      <span class="legend-total" data-testid="legend-beats-of-interest-count">
        <Icon name="activity" size={12} />
        {beatsOfInterest.length}
        {beatsOfInterest.length === 1 ? 'beat' : 'beats'} of interest
      </span>
      <span
        class="legend-review-key"
        data-testid="legend-review-key"
        title="Every marker below follows this: filled once you've judged it, hollow while unreviewed. A rejected beat also gets a strike through it, since it keeps its normal color and shape underneath."
      >
        <svg class="legend-swatch" width="12" height="12" viewBox="-8 -8 16 16" aria-hidden="true">
          <circle cx="0" cy="0" r="6" fill="currentColor" />
        </svg>
        Reviewed
        <svg class="legend-swatch" width="12" height="12" viewBox="-8 -8 16 16" aria-hidden="true">
          <circle cx="0" cy="0" r="5" fill="none" stroke="currentColor" stroke-width="1.5" />
        </svg>
        Unreviewed
        <!-- A rejected beat clears every category flag (see
             rejectedOverlaySeries' own comment), so on its own it would
             render identically to a never-flagged "Normal" beat — this
             strike is the only thing on the chart that still marks it as
             rejected rather than merely normal, so it needs its own key
             entry here rather than being folded into the category legend
             below. -->
        <svg class="legend-swatch" data-testid="legend-rejected-marker" width="12" height="12" viewBox="-8 -8 16 16" aria-hidden="true">
          <circle cx="0" cy="0" r="6" fill="var(--color-success)" />
          <line x1="-4.5" y1="-4.5" x2="4.5" y2="4.5" stroke="var(--color-danger)" stroke-width="2" stroke-linecap="round" />
        </svg>
        Rejected
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
          <!-- "Not yet evaluated" markers are drawn dimmed on the chart
               itself (see displayCategoryColor) — matched here with opacity
               rather than full color so this swatch isn't a darker, more
               solid version of what's actually on screen. -->
          <svg
            class="legend-swatch"
            class:legend-swatch--dimmed={cat === 'unevaluated'}
            width="12"
            height="12"
            viewBox="-8 -8 16 16"
            aria-hidden="true"
          >
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

    <!-- Report labels: the key to the lane under this app's own markers, plus
         its switches. Hiding the lane also hides the disagreement bands and
         the report's bad-data hatching, but not the navigator above. -->
    {#if reportLoaded}
      <div class="ecg-legend-report" data-testid="legend-report" role="group" aria-label="Report labels">
        <span class="legend-report-title">
          <Icon name="file-text" size={12} />
          Report
        </span>
        <button
          type="button"
          class="legend-chip legend-chip--toggle"
          class:legend-chip--inactive={!showReportLane}
          data-testid="legend-report-toggle"
          aria-pressed={showReportLane}
          title={showReportLane ? 'Hide the report lane on the graph' : 'Show the report lane on the graph'}
          onclick={toggleReportLane}
        >
          Show report
        </button>
        <button
          type="button"
          class="legend-chip legend-chip--toggle"
          class:legend-chip--inactive={!showReportNormal}
          data-testid="legend-report-normal-toggle"
          aria-pressed={showReportNormal}
          title="Also draw the report's normal beats (label 0). They are nearly every beat, so they are hidden by default."
          onclick={toggleReportNormal}
        >
          Show normal beats
        </button>
        {#if showReportNormal && reportNormalSkipped}
          <span
            class="legend-report-hint"
            data-testid="report-normal-hint"
            title={`Normal beats are only fetched while at most ${REPORT_NORMAL_MAX_SPAN_SECONDS} s are loaded: there are very many of them.`}
          >
            zoom in to see normal beats
          </span>
        {/if}
        <div class="legend-report-keys" class:legend-report-keys--off={!showReportLane}>
          {#each REPORT_LABELS as label (label)}
            {@const kind = markKindFor(label)}
            <span
              class="legend-report-key"
              class:legend-report-key--dimmed={label === 0 && !showReportNormal}
              data-testid={`legend-report-${kind}`}
              title={REPORT_LABEL_TITLE[label as ReportLabel]}
              style={`--legend-color: var(${REPORT_LABEL_COLOR_VAR[label as ReportLabel]})`}
            >
              <svg class="legend-swatch" width="12" height="12" viewBox="-8 -8 16 16" aria-hidden="true">
                {#if kind === 'arr'}
                  <polygon points="-6,-5 6,-5 0,6" fill="currentColor" />
                {:else if kind === 'pending'}
                  <polygon points="-5,-4 5,-4 0,5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
                {:else if kind === 'rejected'}
                  <path d="M-4.5,-4.5 L4.5,4.5 M4.5,-4.5 L-4.5,4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                {:else}
                  <circle cx="0" cy="0" r="2.5" fill="currentColor" />
                {/if}
              </svg>
              {REPORT_LABEL_NAME[label as ReportLabel]}
            </span>
          {/each}
          <span
            class="legend-report-key"
            data-testid="legend-report-unmatched"
            title={`Dashed: ${REPORT_AGREEMENT_TEXT.unmatched}. Drawn at the report's own timestamp.`}
            style="--legend-color: var(--color-report-unmatched)"
          >
            <svg class="legend-swatch" width="12" height="12" viewBox="-8 -8 16 16" aria-hidden="true">
              <polygon points="-5,-4 5,-4 0,5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2.5 2" stroke-linejoin="round" />
            </svg>
            No beat here
          </span>
          <span
            class="legend-report-key"
            data-testid="legend-report-disagree"
            title={REPORT_FILTER_TITLE.disagreements}
            style="--legend-color: var(--color-report-arr)"
          >
            <svg class="legend-swatch" width="12" height="12" viewBox="-8 -8 16 16" aria-hidden="true">
              <rect x="-3" y="-7" width="6" height="14" fill="var(--color-report-disagree-soft)" stroke="currentColor" stroke-opacity="0.4" />
            </svg>
            Disagreement
          </span>
          {#if reportBadData.length > 0}
            <span
              class="legend-report-key"
              data-testid="legend-report-baddata"
              title="Ranges the report marks as bad data (hatched), separate from the bad data marked in this app (solid red)"
              style="--legend-color: var(--color-report-normal)"
            >
              <svg class="legend-swatch" width="12" height="12" viewBox="-8 -8 16 16" aria-hidden="true">
                <rect x="-8" y="-8" width="16" height="16" fill="currentColor" fill-opacity="0.12" />
                <path d="M-8,2 L2,-8 M-8,8 L8,-8 M-2,8 L8,-2" fill="none" stroke="currentColor" stroke-width="1.2" />
              </svg>
              Report bad data
            </span>
          {/if}
        </div>
        {#if reportFetchFailed}
          <span class="legend-report-error" data-testid="report-lane-error" role="status">
            Report labels could not be loaded for this part of the recording.
          </span>
        {/if}
      </div>
    {/if}
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

  .graph-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    min-height: 120px;
    color: var(--color-text-muted, #5b6b7c);
    font-size: var(--font-size-sm);
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
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1) var(--space-3);
    flex-basis: 100%;
    margin: 0;
    padding: var(--space-1) 0 0;
    border-top: 1px solid var(--color-border, #d3dae1);
    list-style: none;
    font-size: var(--font-size-sm, 0.875rem);
    color: var(--color-text, #1f2933);
  }

  /* Shortcut hints on the left, marker filter on the right. Takes over the
     full-width row + top rule `.nav-hint` has on its own (that variant is
     still used by itself in the focus overlay). */
  .nav-hint-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-1) var(--space-3);
    flex-basis: 100%;
    padding-top: var(--space-1);
    border-top: 1px solid var(--color-border, #d3dae1);
  }

  .nav-hint-row .nav-hint {
    flex-basis: auto;
    padding: 0;
    border-top: none;
  }

  .nav-hint li {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    white-space: nowrap;
  }

  .nav-hint kbd {
    min-width: 1.5em;
    padding: 1px 6px;
    border: 1px solid var(--color-border, #d3dae1);
    border-bottom-width: 2px;
    border-radius: var(--radius-sm, 4px);
    background: var(--color-surface, #fff);
    font-family: var(--font-mono, monospace);
    font-size: 0.8rem;
    font-weight: 600;
    text-align: center;
  }

  .trace-group {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
  }

  .trace-group-label,
  .legend-filter-label {
    font-size: var(--font-size-sm, 0.875rem);
    font-weight: 600;
    color: var(--color-text, #1f2933);
  }

  .trace-group-hint {
    font-size: 0.75rem;
    color: var(--color-text-muted, #5b6b7c);
  }

  .beat-nav-counter {
    min-width: 12rem;
    text-align: center;
    font-size: var(--font-size-sm);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  /* "Model flags": its own boxed group in the flag kind's color (set inline
     as --flag-nav-color/-soft from the current flag), so it is not mistaken
     for the beat-of-interest controls beside it. */
  .flag-nav {
    padding: 2px var(--space-1);
    border: 1px solid var(--flag-nav-color, var(--color-flag));
    border-radius: var(--radius-sm, 4px);
    background: var(--flag-nav-soft, var(--color-flag-soft));
  }

  .flag-nav-label {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: 0 var(--space-1);
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    white-space: nowrap;
    color: var(--flag-nav-color, var(--color-flag));
  }

  .flag-nav-counter {
    min-width: 15rem;
    text-align: center;
    white-space: nowrap;
    font-size: var(--font-size-sm);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .flag-nav-missing {
    font-weight: 400;
    color: var(--color-text-muted);
  }

  /* "Report": the imported report's navigator, boxed in the report's own
     color like "Model flags" is, so the three walks read as three tools. */
  .report-nav {
    padding: 2px var(--space-1);
    border: 1px solid var(--color-report-arr);
    border-radius: var(--radius-sm, 4px);
    background: var(--color-report-disagree-soft);
  }

  .report-nav-label {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: 0 var(--space-1);
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    white-space: nowrap;
    color: var(--color-report-arr);
  }

  .report-filter-select {
    max-width: 14rem;
    padding: 2px var(--space-1);
    border: 1px solid var(--color-border, #d3dae1);
    border-radius: var(--radius-sm, 4px);
    background: var(--color-surface, #fff);
    color: var(--color-text, #1f2933);
    font-size: var(--font-size-sm);
  }

  .report-nav-counter {
    min-width: 7rem;
    text-align: center;
    white-space: nowrap;
    font-size: var(--font-size-sm);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .report-nav-missing {
    font-weight: 400;
    color: var(--color-text-muted);
  }

  .ecg-legend-report {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1) var(--space-2);
    flex-basis: 100%;
    padding-top: var(--space-1);
    border-top: 1px solid var(--color-border, #d3dae1);
  }

  .legend-report-title {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-muted);
  }

  .legend-chip--toggle {
    border-color: var(--color-border, #d3dae1);
  }

  .legend-report-keys {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1) var(--space-2);
  }

  .legend-report-keys--off {
    opacity: 0.45;
  }

  .legend-report-key {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 0.7rem;
    color: var(--color-text-muted);
  }

  .legend-report-key--dimmed {
    opacity: 0.45;
  }

  .legend-report-hint {
    font-size: 0.7rem;
    font-style: italic;
    color: var(--color-text-muted);
  }

  .legend-report-error {
    font-size: 0.7rem;
    color: var(--color-warning, #b45309);
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

  .x-range-slider {
    width: 100%;
    margin: var(--space-1) 0 0;
    accent-color: var(--color-accent, #2563eb);
    cursor: ew-resize;
  }

  .x-range-slider:disabled {
    cursor: default;
    opacity: 0.5;
  }

  .mark-editor {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2);
    border: 1px solid var(--color-danger, #b91c1c);
    border-radius: var(--radius-sm, 4px);
    background: var(--color-surface-raised, #f7f9fb);
    font-size: var(--font-size-sm, 0.875rem);
  }

  .mark-editor-title {
    font-weight: 600;
  }

  .mark-editor label {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
  }

  .mark-editor input {
    width: 6.5rem;
  }

  .mark-editor-hint {
    font-size: 0.75rem;
  }

  .bad-data-mark.selected {
    outline: 2px solid var(--color-danger, #b91c1c);
    outline-offset: -2px;
  }

  .graph-stage {
    position: relative;
  }

  .focus-overlay {
    position: absolute;
    z-index: 1;
    max-height: calc(100% - var(--space-4, 16px));
    overflow-y: auto;
    padding: var(--space-2);
    border: 1px solid var(--color-border, #d3dae1);
    border-radius: var(--radius-md, 8px);
    background: var(--color-surface, #fff);
    box-shadow: var(--shadow-md);
  }

  .focus-overlay-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: var(--space-1);
  }

  .focus-overlay-title {
    font-weight: 600;
  }

  .focus-overlay-keys {
    margin-top: var(--space-2);
  }

  /* Its only children are absolutely positioned marks, so it has no content
     width of its own: without `flex: 1` it collapsed to 0px inside the row
     and the strip was invisible. */
  .bad-data-marks-bar {
    position: relative;
    flex: 1;
    min-width: 0;
    height: var(--space-3, 12px);
    background: var(--color-border, #d3dae1);
    border-radius: var(--radius-sm, 4px);
    overflow: hidden;
  }

  .bad-data-mark {
    position: absolute;
    top: 0;
    bottom: 0;
    /* Matches the shading painted on the chart itself and the count badge
       beside this strip (both red/danger) — this used to be the amber
       warning color, which made the same bad-data range look like two
       different things depending on which part of the graph you looked at. */
    background: var(--color-danger, #b91c1c);
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

  .legend-review-key {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px var(--space-2);
    font-size: 0.7rem;
    color: var(--color-text-muted);
  }

  /* Matches the 55% opacity `displayCategoryColor` renders "Not yet
     evaluated" markers at on the chart (see that function). */
  .legend-swatch--dimmed {
    opacity: 0.55;
  }

  .ecg-legend-review-filter {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1);
    margin-left: auto;
  }
</style>
