// The "report lane": an imported report's per-beat labels drawn read-only under
// the app's own beat-marker lane (see EcgGraph.svelte), plus the pure helpers
// the report navigator needs. Everything that decides WHAT is drawn or WHERE
// the navigator goes lives here as plain functions; the canvas drawing is a
// uPlot plugin factory that reads its state through a getter, so it always
// paints the latest report beats and toggles without being rebuilt.

import uPlot from 'uplot'
import type { ReportBeat, ReportFilter, ReportLabel } from '../../lib/api/types'
import { REPORT_LABEL_COLOR_VAR } from '../../lib/reports'

// ---- Layout -----------------------------------------------------------------

// The report lane sits directly under the app's own marker lane (y = 0.94 on
// the same 0..1 lane scale).
export const REPORT_LANE_Y = 0.86
// The y scale's headroom above the trace while the report lane is shown (see
// MARKER_LANE_HEADROOM): the second lane, and the flag tags hanging under it,
// need more room than the single lane does.
export const REPORT_LANE_HEADROOM = 0.38
// A report beat's marker, in CSS pixels at 1x zoom (ours are 6 / 9).
export const REPORT_MARK_SIZE = 8
// Width of the soft band behind a beat the report and this review disagree on.
export const REPORT_DISAGREE_BAND_PX = 6
// Spacing of the diagonal hatching over the report's bad-data ranges.
export const REPORT_HATCH_SPACING_PX = 7
// Navigation targets are the report's own timestamps; this is how close counts
// as "the same beat" when a timestamp came from somewhere else. Exact matches
// always win, so two targets closer than this stay distinct.
export const REPORT_TARGET_TOLERANCE_SECONDS = 0.002

// Normal (label 0) report beats are nearly every beat: asking the backend for
// all of them over a long stretch of a long recording (~70k beats in 2 h) takes
// seconds to serialise. So they are requested only for a window this narrow;
// a wider one is fetched without them and the legend says to zoom in.
export const REPORT_NORMAL_MAX_SPAN_SECONDS = 120
// The most a normal-beat fetch may cover: the graph loads three times the
// visible width (see PAD_FACTOR), with slack for rounding. The visible width
// alone decides whether they are wanted; this only keeps a window the graph
// has loaded for the whole file (before its opening view arrives) from asking.
export const REPORT_NORMAL_MAX_FETCH_SPAN_SECONDS = 4 * REPORT_NORMAL_MAX_SPAN_SECONDS

// Past this many markers per CSS pixel of plot width the lane is drawn as one
// marker per pixel column (the most severe label in it) instead of one per
// beat: beats that close are indistinguishable anyway, and a long recording
// has tens of thousands of them in view when fully zoomed out.
export const REPORT_DENSE_MARKERS_PER_PX = 0.5

export const REPORT_FILTER_STORAGE_KEY = 'pat.report.filter'
export const REPORT_LANE_STORAGE_KEY = 'pat.report.lane'
export const REPORT_NORMAL_STORAGE_KEY = 'pat.report.normal'

// ---- Pure helpers ---------------------------------------------------------------

export type ReportMarkKind = 'arr' | 'pending' | 'rejected' | 'normal'

/** The marker a label is drawn as, or null for a label this build does not know. */
export function markKindFor(label: ReportLabel | number): ReportMarkKind | null {
  switch (label) {
    case 2:
      return 'arr'
    case 1:
      return 'pending'
    case -1:
      return 'rejected'
    case 0:
      return 'normal'
    default:
      return null
  }
}

/** A beat the report and this review disagree about whether it is an arrhythmia. */
export function isDisagreement(beat: Pick<ReportBeat, 'agreement'>): boolean {
  return beat.agreement === 'report_only' || beat.agreement === 'ours_only'
}

/** Normal (label 0) beats are drawn only on request: they are nearly every beat. */
export function isLaneBeatShown(beat: Pick<ReportBeat, 'label'>, showNormal: boolean): boolean {
  return beat.label !== 0 || showNormal
}

/**
 * Position of `ts` in the ascending `targets`. Exact first — a target is
 * normally one of the list's own timestamps, and two closer together than the
 * tolerance must stay distinct or stepping would resolve the second back to
 * the first and never get past it — then the nearest within the tolerance.
 * -1 when `ts` is not (any longer) in the list.
 */
export function indexOfTarget(targets: readonly number[] | undefined | null, ts: number | null): number {
  if (!targets || ts === null || !Number.isFinite(ts)) return -1
  const exact = targets.indexOf(ts)
  if (exact >= 0) return exact
  let nearest = -1
  let nearestDist = REPORT_TARGET_TOLERANCE_SECONDS
  for (let i = 0; i < targets.length; i++) {
    const dist = Math.abs(targets[i] - ts)
    if (dist < nearestDist) {
      nearest = i
      nearestDist = dist
    }
  }
  return nearest
}

/**
 * The index to jump to when stepping from `ts` in `direction`, wrapping at both
 * ends; -1 when there are no targets. From a `ts` that is no longer a target
 * (the technician just resolved it and the list shrank) it steps to the
 * neighbour in time rather than back to the start of the list, so a review
 * loop carries on from where it was.
 */
export function stepTarget(
  targets: readonly number[] | undefined | null,
  ts: number | null,
  direction: 1 | -1,
): number {
  const count = targets?.length ?? 0
  if (!targets || count === 0) return -1
  const at = indexOfTarget(targets, ts)
  if (at >= 0) {
    if (direction > 0) return at + 1 >= count ? 0 : at + 1
    return at <= 0 ? count - 1 : at - 1
  }
  if (ts === null || !Number.isFinite(ts)) return direction > 0 ? 0 : count - 1
  if (direction > 0) {
    const next = targets.findIndex((target) => target > ts)
    return next >= 0 ? next : 0
  }
  for (let i = count - 1; i >= 0; i--) {
    if (targets[i] < ts) return i
  }
  return count - 1
}

/** Whether a report window [start, end] is narrow enough to ask for the normal beats too. */
export function allowsNormalBeats(start: number, end: number): boolean {
  const span = end - start
  return Number.isFinite(span) && span <= REPORT_NORMAL_MAX_SPAN_SECONDS
}

/**
 * Whether a fetch of [fetchStart, fetchEnd] should ask for the normal beats
 * too. Decided by the width of the VISIBLE view [min, max] — what the
 * technician is looking at — not by the buffer loaded around it, which is three
 * times as wide; the fetch's own span is only bounded so the graph's
 * whole-file first window never asks.
 */
export function allowsNormalFetch(min: number, max: number, fetchStart: number, fetchEnd: number): boolean {
  if (!allowsNormalBeats(min, max)) return false
  const span = fetchEnd - fetchStart
  return Number.isFinite(span) && span <= REPORT_NORMAL_MAX_FETCH_SPAN_SECONDS
}

/** Where the navigator starts when the technician has never chosen a filter. */
export function defaultReportFilter(targets: Partial<Record<ReportFilter, number[]>> | undefined | null): ReportFilter {
  return (targets?.disagreements?.length ?? 0) > 0 ? 'disagreements' : 'arrhythmia'
}

/** True once the navigation's targets have arrived (any filter has a list, even an empty one). */
export function hasReportTargets(targets: Partial<Record<ReportFilter, number[]>> | undefined | null): boolean {
  if (!targets) return false
  return (Object.keys(targets) as ReportFilter[]).some((filter) => Array.isArray(targets[filter]))
}

/**
 * The filter a report starts on: the technician's remembered choice while it
 * still has beats to step through, else the default. A remembered filter with
 * nothing in it for THIS report would otherwise leave the navigator looking
 * dead.
 */
export function resolveReportFilter(
  remembered: ReportFilter | null,
  targets: Partial<Record<ReportFilter, number[]>> | undefined | null,
): ReportFilter {
  if (remembered !== null && (targets?.[remembered]?.length ?? 0) > 0) return remembered
  return defaultReportFilter(targets)
}

/** The report beat at `ts` — exact, else the nearest within the tolerance. */
export function findReportBeat(beats: readonly ReportBeat[], ts: number): ReportBeat | undefined {
  let nearest: ReportBeat | undefined
  let nearestDist = REPORT_TARGET_TOLERANCE_SECONDS
  for (const beat of beats) {
    if (beat.ts === ts) return beat
    const dist = Math.abs(beat.ts - ts)
    if (dist < nearestDist) {
      nearest = beat
      nearestDist = dist
    }
  }
  return nearest
}

/** The ranges that overlap [min, max], each with start <= stop. */
export function visibleBadData(
  ranges: readonly { start: number; stop: number }[] | undefined | null,
  min: number,
  max: number,
): { start: number; stop: number }[] {
  const visible: { start: number; stop: number }[] = []
  for (const range of ranges ?? []) {
    const start = Math.min(range.start, range.stop)
    const stop = Math.max(range.start, range.stop)
    if (!Number.isFinite(start) || !Number.isFinite(stop)) continue
    if (stop < min || start > max) continue
    visible.push({ start, stop })
  }
  return visible
}

/**
 * Which diagonal lines of a hatch cover the pixel span [clipLeft, clipRight]:
 * line k runs from (anchor + k * spacing, bottom) up to that x plus `height`.
 * Anchored to the range's own left edge so the hatching moves with the range
 * when the view pans, and bounded by the plot width however far off-screen the
 * range itself starts.
 */
export function hatchLineRange(
  anchor: number,
  clipLeft: number,
  clipRight: number,
  height: number,
  spacing: number,
): { first: number; last: number } {
  return {
    first: Math.ceil((clipLeft - height - anchor) / spacing),
    last: Math.floor((clipRight - anchor) / spacing),
  }
}

// ---- Persisted preferences ---------------------------------------------------------

export function parseStoredReportFilter(raw: string | null, valid: readonly ReportFilter[]): ReportFilter | null {
  return raw !== null && (valid as readonly string[]).includes(raw) ? (raw as ReportFilter) : null
}

export function loadStoredString(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    // Unreadable storage just means "no remembered choice".
    return null
  }
}

export function storeString(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Not being able to remember a choice is not worth surfacing.
  }
}

export function loadStoredBool(key: string, fallback: boolean): boolean {
  const raw = loadStoredString(key)
  return raw === '1' ? true : raw === '0' ? false : fallback
}

// ---- Drawing ----------------------------------------------------------------------

/** The subset of a canvas context (or Path2D) the shapes are built from. */
export interface PathSink {
  moveTo(x: number, y: number): void
  lineTo(x: number, y: number): void
  arc(x: number, y: number, radius: number, start: number, end: number): void
  closePath(): void
}

/**
 * One marker centred at (cx, cy), "radius" r, into `sink`. Shape carries the
 * label as well as color so the four stay apart in the black & white theme: a
 * down-pointing triangle (filled = confirmed, hollow = not reviewed), a cross
 * (rejected) and a small dot (normal). The triangles point DOWN so they are
 * never mistaken for the up-pointing 'triangle' of one of the app's own
 * marker categories on the lane above.
 */
export function addReportShape(sink: PathSink, kind: ReportMarkKind, cx: number, cy: number, r: number): void {
  switch (kind) {
    case 'arr':
    case 'pending': {
      const halfWidth = r * 1.05
      const top = cy - r * 0.8
      sink.moveTo(cx - halfWidth, top)
      sink.lineTo(cx + halfWidth, top)
      sink.lineTo(cx, cy + r * 0.95)
      sink.closePath()
      break
    }
    case 'rejected': {
      const s = r * 0.8
      sink.moveTo(cx - s, cy - s)
      sink.lineTo(cx + s, cy + s)
      sink.moveTo(cx + s, cy - s)
      sink.lineTo(cx - s, cy + s)
      break
    }
    case 'normal': {
      const dot = r * 0.45
      sink.moveTo(cx + dot, cy)
      sink.arc(cx, cy, dot, 0, Math.PI * 2)
      break
    }
  }
}

export interface ReportLaneState {
  /** Draw anything at all: a report is attached and its lane is switched on. */
  active: boolean
  beats: readonly ReportBeat[]
  showNormal: boolean
  badData: readonly { start: number; stop: number }[]
  /** The report beat the navigator is on, ringed in the lane. */
  currentTs: number | null
}

export interface ReportLaneDeps {
  /** Resolves a CSS custom property to a literal color (canvas cannot use `var()`). */
  cssVar: (name: string, fallback: string) => string
  /** The uPlot scale the marker lanes live on, and the y of the app's own lane. */
  laneScale: string
  ourLaneY: number
  /** The zoom-dependent marker size multiplier (see `visibleMarkerScale`). */
  markerScale: (u: uPlot) => number
  /**
   * Changes whenever what `cssVar` resolves to would (the theme). With it the
   * plugin looks each custom property up once per value instead of at every
   * draw — a computed-style lookup is the slowest thing a redraw does, and a
   * pan redraws many times a second.
   */
  themeKey?: () => string
}

export interface ReportColors {
  arr: string
  pending: string
  rejected: string
  normal: string
  unmatched: string
  disagreeSoft: string
}

// The light-theme values from tokens.css, for when a custom property cannot be
// resolved (jsdom).
const FALLBACK_COLORS: ReportColors = {
  arr: '#be123c',
  pending: '#b45309',
  rejected: '#64748b',
  normal: '#94a3b8',
  unmatched: '#7c3aed',
  disagreeSoft: 'rgba(190, 18, 60, 0.12)',
}

/** Every color a draw needs, resolved once (one computed-style lookup each). */
export function resolveReportColors(resolve: ReportLaneDeps['cssVar']): ReportColors {
  return {
    arr: resolve(REPORT_LABEL_COLOR_VAR[2], FALLBACK_COLORS.arr),
    pending: resolve(REPORT_LABEL_COLOR_VAR[1], FALLBACK_COLORS.pending),
    rejected: resolve(REPORT_LABEL_COLOR_VAR[-1], FALLBACK_COLORS.rejected),
    normal: resolve(REPORT_LABEL_COLOR_VAR[0], FALLBACK_COLORS.normal),
    unmatched: resolve('--color-report-unmatched', FALLBACK_COLORS.unmatched),
    disagreeSoft: resolve('--color-report-disagree-soft', FALLBACK_COLORS.disagreeSoft),
  }
}

function pixelRatio(): number {
  return uPlot.pxRatio || 1
}

function viewRange(u: uPlot): { min: number; max: number } | null {
  const { min, max } = u.scales.x
  return min != null && max != null && Number.isFinite(min) && Number.isFinite(max) && max > min ? { min, max } : null
}

/**
 * Behind the trace: the report's bad-data ranges as a diagonal hatch (so they
 * never read as the app's own solid red marks) and a soft band at every beat
 * the report and this review disagree about.
 */
export function drawReportBackdrop(u: uPlot, state: ReportLaneState, deps: ReportLaneDeps): void {
  if (!state.active) return
  const view = viewRange(u)
  if (!view) return
  const bad = visibleBadData(state.badData, view.min, view.max)
  const bands: number[] = []
  for (const beat of state.beats) {
    if (beat.ts >= view.min && beat.ts <= view.max && isDisagreement(beat)) bands.push(beat.ts)
  }
  if (bad.length === 0 && bands.length === 0) return

  const { ctx } = u
  const dpr = pixelRatio()
  const colors = resolveReportColors(deps.cssVar)
  const { left, top, width, height } = u.bbox
  ctx.save()
  try {
    ctx.beginPath()
    ctx.rect(left, top, width, height)
    ctx.clip()

    if (bad.length > 0) {
      const spacing = REPORT_HATCH_SPACING_PX * dpr
      for (const range of bad) {
        const from = u.valToPos(range.start, 'x', true)
        const to = u.valToPos(range.stop, 'x', true)
        if (!Number.isFinite(from) || !Number.isFinite(to)) continue
        // At least a hairline wide, so a very short range stays visible.
        const clipLeft = Math.max(from, left)
        const clipRight = Math.max(Math.min(to, left + width), clipLeft + 2 * dpr)
        ctx.save()
        try {
          ctx.beginPath()
          ctx.rect(clipLeft, top, clipRight - clipLeft, height)
          ctx.clip()
          ctx.fillStyle = colors.normal
          ctx.strokeStyle = colors.normal
          ctx.globalAlpha = 0.1
          ctx.fillRect(clipLeft, top, clipRight - clipLeft, height)
          ctx.globalAlpha = 0.5
          ctx.lineWidth = dpr
          ctx.beginPath()
          const { first, last } = hatchLineRange(from, clipLeft, clipRight, height, spacing)
          for (let k = first; k <= last; k++) {
            const x = from + k * spacing
            ctx.moveTo(x, top + height)
            ctx.lineTo(x + height, top)
          }
          ctx.stroke()
          // The range's own edges, so its extent reads at a glance.
          ctx.globalAlpha = 0.7
          ctx.beginPath()
          ctx.moveTo(from + dpr / 2, top)
          ctx.lineTo(from + dpr / 2, top + height)
          ctx.moveTo(to - dpr / 2, top)
          ctx.lineTo(to - dpr / 2, top + height)
          ctx.stroke()
        } finally {
          ctx.restore()
        }
      }
    }

    if (bands.length > 0) {
      const half = (REPORT_DISAGREE_BAND_PX * dpr) / 2
      ctx.fillStyle = colors.disagreeSoft
      // One band per pixel column: a second one in the same column paints the
      // same pixels again (and, being translucent, darkens them).
      const painted = new Uint8Array(Math.ceil(width) + 2)
      for (const ts of bands) {
        const x = u.valToPos(ts, 'x', true)
        if (!Number.isFinite(x)) continue
        const column = Math.floor(x - left)
        if (column >= 0 && column < painted.length) {
          if (painted[column]) continue
          painted[column] = 1
        }
        ctx.fillRect(x - half, top, half * 2, height)
      }
    }
  } finally {
    ctx.restore()
  }
}

/** On top of the series: the report's markers, the current beat's ring and the lane labels. */
export function drawReportLane(u: uPlot, state: ReportLaneState, deps: ReportLaneDeps): void {
  if (!state.active) return
  const view = viewRange(u)
  if (!view) return

  const { ctx } = u
  const dpr = pixelRatio()
  const colors = resolveReportColors(deps.cssVar)
  const { left, top, width, height } = u.bbox
  const laneY = u.valToPos(REPORT_LANE_Y, deps.laneScale, true)
  const ourLaneY = u.valToPos(deps.ourLaneY, deps.laneScale, true)
  const markerScale = deps.markerScale(u)
  const r = (REPORT_MARK_SIZE * markerScale * dpr) / 2

  // Markers, batched into one path per look (kind x matched/unmatched).
  const groups = collectLaneGroups(u, state, view, dpr)

  ctx.save()
  try {
    ctx.beginPath()
    ctx.rect(left, top, width, height)
    ctx.clip()

    for (const { kind, unmatched, xs } of groups) {
      ctx.beginPath()
      for (const x of xs) addReportShape(ctx, kind, x, laneY, r)
      if (unmatched) {
        ctx.setLineDash([2.5 * dpr, 2 * dpr])
        ctx.strokeStyle = colors.unmatched
        ctx.lineWidth = (kind === 'arr' ? 2 : 1.5) * dpr
        ctx.stroke()
        ctx.setLineDash([])
        continue
      }
      const color = colors[kind]
      ctx.fillStyle = color
      ctx.strokeStyle = color
      if (kind === 'arr') {
        ctx.lineWidth = dpr
        ctx.fill()
        ctx.stroke()
      } else if (kind === 'normal') {
        ctx.fill()
      } else {
        ctx.lineWidth = (kind === 'rejected' ? 2 : 1.5) * dpr
        ctx.lineCap = 'round'
        ctx.stroke()
        ctx.lineCap = 'butt'
      }
    }

    // The beat the navigator is on.
    let ringX: number | null = null
    if (state.currentTs !== null && state.currentTs >= view.min && state.currentTs <= view.max) {
      const x = u.valToPos(state.currentTs, 'x', true)
      if (Number.isFinite(x)) {
        ringX = x
        ctx.strokeStyle = deps.cssVar('--color-accent', '#2563eb')
        ctx.lineWidth = 2 * dpr
        ctx.beginPath()
        ctx.arc(x, laneY, r * 1.9, 0, Math.PI * 2)
        ctx.stroke()
      }
    }

    // Which lane is which, on the left edge of the plot — but never on top of
    // a marker: a label whose box would cover one of either lane's markers is
    // left out (the legend below names the lanes too).
    ctx.font = `${10 * dpr}px ${deps.cssVar('--font-sans', 'system-ui, sans-serif')}`
    ctx.textAlign = 'left'
    ctx.textBaseline = 'middle'
    const surface = deps.cssVar('--color-surface', '#ffffff')
    const muted = deps.cssVar('--color-text-muted', '#5b6b7c')
    const padX = 3 * dpr
    const boxLeft = left + 2 * dpr
    const boxHalfHeight = 7 * dpr
    const labels = (
      [
        ['This app', ourLaneY],
        ['Report', laneY],
      ] as const
    ).map(([text, y]) => ({ text, y, boxWidth: ctx.measureText(text).width + padX * 2 }))
    const boxRight = boxLeft + Math.max(...labels.map((label) => label.boxWidth))

    // Our markers are a little bigger than the report's (see `categoryMarkerSeries`).
    const ourRadius = Math.max(9, 4.5 * dpr) * markerScale + dpr
    const rows: { y: number; radius: number; xs: readonly number[] }[] = [
      { y: ourLaneY, radius: ourRadius, xs: ourLaneMarkerXs(u, deps.laneScale, view, boxLeft - ourRadius, boxRight + ourRadius) },
      { y: laneY, radius: r + dpr, xs: groups.flatMap((group) => group.xs) },
    ]
    if (ringX !== null) rows.push({ y: laneY, radius: r * 1.9 + dpr, xs: [ringX] })

    for (const { text, y, boxWidth } of labels) {
      const covers = rows.some(
        (row) =>
          Math.abs(row.y - y) < boxHalfHeight + row.radius &&
          row.xs.some((x) => x >= boxLeft - row.radius && x <= boxLeft + boxWidth + row.radius),
      )
      if (covers) continue
      ctx.globalAlpha = 0.85
      ctx.fillStyle = surface
      ctx.fillRect(boxLeft, y - boxHalfHeight, boxWidth, 14 * dpr)
      ctx.globalAlpha = 1
      ctx.fillStyle = muted
      ctx.fillText(text, boxLeft + padX, y + 0.5 * dpr)
    }
  } finally {
    ctx.restore()
  }
}

// ---- Lane helpers -----------------------------------------------------------------

interface LaneGroup {
  kind: ReportMarkKind
  /** A report beat none of ours matches: drawn dashed and hollow. */
  unmatched: boolean
  /** Canvas x of each marker. */
  xs: number[]
}

// Severity of a look, for the dense draw: the label that matters most wins a
// pixel column. Unmatched wins over matched at the same severity.
const KIND_BY_RANK: readonly ReportMarkKind[] = ['normal', 'rejected', 'pending', 'arr']
const KIND_RANK: Record<ReportMarkKind, number> = { normal: 1, rejected: 2, pending: 3, arr: 4 }

/**
 * The markers to draw, one group per look (kind x matched/unmatched), in
 * ascending severity so the labels that matter are painted last, on top.
 *
 * Normally one marker per report beat in view. When they are denser than
 * `REPORT_DENSE_MARKERS_PER_PX` — a long recording zoomed all the way out has
 * tens of thousands in view — it is one marker per pixel column instead, the
 * most severe label in it: beats that close cannot be told apart anyway, and
 * the cost of a draw then follows the width of the plot, not the beat count.
 */
function collectLaneGroups(
  u: uPlot,
  state: ReportLaneState,
  view: { min: number; max: number },
  dpr: number,
): LaneGroup[] {
  const { left, width } = u.bbox
  const span = view.max - view.min
  const pad = span * 0.02
  const lo = view.min - pad
  const hi = view.max + pad
  const beats = state.beats
  const cssWidth = width / dpr
  const limit = Math.max(1, Math.floor(cssWidth * REPORT_DENSE_MARKERS_PER_PX))

  // Whether it is dense — counting stops as soon as it is.
  let count = 0
  let dense = false
  for (let i = 0; i < beats.length; i++) {
    const beat = beats[i]
    if (!(beat.ts >= lo && beat.ts <= hi)) continue
    if (!isLaneBeatShown(beat, state.showNormal) || markKindFor(beat.label) === null) continue
    if (++count > limit) {
      dense = true
      break
    }
  }
  if (count === 0) return []

  const slots: number[][] = Array.from({ length: KIND_BY_RANK.length * 2 }, () => [])
  const slotOf = (rank: number, unmatched: boolean) => (unmatched ? KIND_BY_RANK.length : 0) + rank - 1

  if (!dense) {
    for (const beat of beats) {
      if (!(beat.ts >= lo && beat.ts <= hi)) continue
      if (!isLaneBeatShown(beat, state.showNormal)) continue
      const kind = markKindFor(beat.label)
      if (kind === null) continue
      const x = u.valToPos(beat.ts, 'x', true)
      if (!Number.isFinite(x)) continue
      // A report beat none of ours matches is drawn at its own timestamp, dashed
      // and hollow in its own color, so it cannot be taken for a matched one.
      slots[slotOf(KIND_RANK[kind], beat.match === 'none')].push(x)
    }
  } else {
    // The x scale is linear, so a position is one multiply-add.
    const perSecond = width / span
    const padColumns = Math.ceil(cssWidth * 0.02) + 2
    const best = new Uint8Array(Math.ceil(cssWidth) + 2 * padColumns)
    for (const beat of beats) {
      if (!(beat.ts >= lo && beat.ts <= hi)) continue
      if (!isLaneBeatShown(beat, state.showNormal)) continue
      const kind = markKindFor(beat.label)
      if (kind === null) continue
      const column = Math.floor(((beat.ts - view.min) * perSecond) / dpr) + padColumns
      if (column < 0 || column >= best.length) continue
      const code = KIND_RANK[kind] * 2 + (beat.match === 'none' ? 1 : 0)
      if (code > best[column]) best[column] = code
    }
    for (let column = 0; column < best.length; column++) {
      const code = best[column]
      if (code === 0) continue
      slots[slotOf(code >> 1, (code & 1) === 1)].push(left + (column - padColumns + 0.5) * dpr)
    }
  }

  const groups: LaneGroup[] = []
  slots.forEach((xs, slot) => {
    if (xs.length === 0) return
    const unmatched = slot >= KIND_BY_RANK.length
    groups.push({ kind: KIND_BY_RANK[slot % KIND_BY_RANK.length], unmatched, xs })
  })
  return groups
}

/** First index in the ascending `values` that is >= `target`. */
function lowerBound(values: ArrayLike<number>, target: number): number {
  let lo = 0
  let hi = values.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (values[mid] < target) lo = mid + 1
    else hi = mid
  }
  return lo
}

/**
 * Canvas x of every marker of the app's own lane (every shown series on the
 * lane scale) between canvas x `fromPx` and `toPx` — what the lane labels must
 * keep clear of. Reads the chart's data, so it follows the legend's filters.
 */
function ourLaneMarkerXs(
  u: uPlot,
  laneScale: string,
  view: { min: number; max: number },
  fromPx: number,
  toPx: number,
): number[] {
  const data = u.data as ArrayLike<ArrayLike<number | null> | undefined> | undefined
  const xs = data?.[0] as ArrayLike<number> | undefined
  if (!xs || !u.series) return []
  const { left, width } = u.bbox
  const secondsPerPx = (view.max - view.min) / width
  const from = view.min + (fromPx - left) * secondsPerPx
  const to = view.min + (toPx - left) * secondsPerPx
  const first = lowerBound(xs, from)
  const found: number[] = []
  for (let s = 1; s < u.series.length; s++) {
    const series = u.series[s]
    const ys = data?.[s]
    if (!ys || series.scale !== laneScale || series.show === false) continue
    for (let i = first; i < xs.length && xs[i] <= to; i++) {
      if (ys[i] != null) found.push(left + (xs[i] - view.min) / secondsPerPx)
    }
  }
  return found
}

/**
 * The uPlot plugin. `getState` is read on every draw, so toggles, new report
 * beats and a changed current beat show up on the next redraw without
 * rebuilding the chart. Decoration only: whatever goes wrong while painting
 * must never take the trace, the beat markers or any interaction down with it.
 */
export function reportLanePlugin(getState: () => ReportLaneState, deps: ReportLaneDeps): uPlot.Plugin {
  // Custom properties resolved once per theme (`deps.themeKey`), not per draw.
  const resolved = new Map<string, string>()
  let resolvedFor: string | undefined
  const cached: ReportLaneDeps = deps.themeKey
    ? {
        ...deps,
        cssVar: (name, fallback) => {
          const key = `${name}|${fallback}`
          let value = resolved.get(key)
          if (value === undefined) {
            value = deps.cssVar(name, fallback)
            resolved.set(key, value)
          }
          return value
        },
      }
    : deps
  const syncTheme = () => {
    if (!deps.themeKey) return
    const key = deps.themeKey()
    if (key !== resolvedFor) {
      resolvedFor = key
      resolved.clear()
    }
  }

  return {
    hooks: {
      // Behind the waveform, like the bad-data shading, so neither the hatch
      // nor the bands tint the trace being judged.
      drawClear: (u) => {
        try {
          syncTheme()
          drawReportBackdrop(u, getState(), cached)
        } catch {
          // ignore — see above
        }
      },
      draw: (u) => {
        try {
          syncTheme()
          drawReportLane(u, getState(), cached)
        } catch {
          // ignore — see above
        }
      },
    },
  }
}
