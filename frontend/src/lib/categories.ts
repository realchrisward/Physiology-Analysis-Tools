// Shared arrhythmia-category constants — labels, colors, and marker shapes
// — used by both the graph (EcgGraph.svelte, for marker rendering plus its
// legend/filter) and the beat review panel (BeatCategoryPanel.svelte, for
// its chips), so the same category always looks the same everywhere in the
// UI, per the explicit request to "co-relate" graph markers with the
// category controls.

export const REASSIGNABLE_CATEGORIES = [
  'bradycardia_absolute',
  'tachycardia_absolute',
  'skipped_beat',
  'prem_beat',
  'abn_cluster',
  'other_arrhythmia',
] as const

export type ReassignableCategory = (typeof REASSIGNABLE_CATEGORIES)[number]

export const CATEGORY_LABELS: Record<ReassignableCategory, string> = {
  bradycardia_absolute: 'Bradycardia (absolute)',
  tachycardia_absolute: 'Tachycardia (absolute)',
  skipped_beat: 'Skipped beat',
  prem_beat: 'Premature beat',
  abn_cluster: 'Abnormal cluster',
  other_arrhythmia: 'Other arrhythmia',
}

// The CSS custom property (see tokens.css) holding this category's marker
// color, one value per theme (light/dark/bw) the same way the rest of the
// app's palette works — resolved at draw time via getComputedStyle, same
// pattern EcgGraph already uses for every other graph color.
export const CATEGORY_COLOR_VAR: Record<ReassignableCategory, string> = {
  bradycardia_absolute: '--color-cat-bradycardia',
  tachycardia_absolute: '--color-cat-tachycardia',
  skipped_beat: '--color-cat-skipped',
  prem_beat: '--color-cat-premature',
  abn_cluster: '--color-cat-cluster',
  other_arrhythmia: '--color-cat-other',
}

export type MarkerShape = 'circle' | 'square' | 'triangle' | 'diamond' | 'cross' | 'plus'

// Shape is the colorblind/black-&-white-theme-safe distinguisher — color
// alone is never the only signal telling two categories apart.
export const CATEGORY_SHAPE: Record<ReassignableCategory, MarkerShape> = {
  bradycardia_absolute: 'circle',
  tachycardia_absolute: 'triangle',
  skipped_beat: 'square',
  prem_beat: 'diamond',
  abn_cluster: 'cross',
  other_arrhythmia: 'plus',
}

export type DisplayCategory = 'normal' | 'unevaluated' | ReassignableCategory

// Fixed render/legend/filter order: the two non-arrhythmia states first,
// then every specific arrhythmia category in the same order used
// throughout the rest of the app (BeatCategoryPanel's reassign list, etc).
export const DISPLAY_CATEGORIES: DisplayCategory[] = ['normal', 'unevaluated', ...REASSIGNABLE_CATEGORIES]

export const DISPLAY_CATEGORY_LABELS: Record<DisplayCategory, string> = {
  normal: 'Normal',
  unevaluated: 'Not yet evaluated',
  ...CATEGORY_LABELS,
}

export const DISPLAY_CATEGORY_SHAPE: Record<DisplayCategory, MarkerShape> = {
  normal: 'circle',
  unevaluated: 'circle',
  ...CATEGORY_SHAPE,
}

interface CategoryFlags {
  any_arrhythmia?: boolean | null
  bradycardia_absolute?: boolean | null
  tachycardia_absolute?: boolean | null
  skipped_beat?: boolean | null
  prem_beat?: boolean | null
  abn_cluster?: boolean | null
  other_arrhythmia?: boolean | null
}

// Every (category, reviewed) combination the graph renders as its own uPlot
// marker series — 16 total (8 display categories × reviewed/unreviewed).
// Fixed order/keys shared between `buildBeatAlignedData` (which produces
// one y-array per key) and the chart's own series/data array construction,
// so the two always line up positionally.
export interface MarkerBucket {
  category: DisplayCategory
  reviewed: boolean
  key: string
}

export const MARKER_BUCKETS: MarkerBucket[] = DISPLAY_CATEGORIES.flatMap((category) => [
  { category, reviewed: false, key: `${category}|unreviewed` },
  { category, reviewed: true, key: `${category}|reviewed` },
])

/**
 * The ONE category used to color/shape a beat's marker on the graph. A beat
 * can in principle have multiple category flags set at once (the detector
 * doesn't enforce exclusivity), but a marker can only show one color/shape
 * — picked by a fixed priority (REASSIGNABLE_CATEGORIES' own order),
 * arbitrary but consistent, so the same beat always renders the same way.
 */
export function primaryDisplayCategory(beat: CategoryFlags): DisplayCategory {
  if (beat.any_arrhythmia === null || beat.any_arrhythmia === undefined) return 'unevaluated'
  if (beat.any_arrhythmia === false) return 'normal'
  for (const cat of REASSIGNABLE_CATEGORIES) {
    if (beat[cat] === true) return cat
  }
  // any_arrhythmia is true but no specific column is — shouldn't normally
  // happen, but falls into a real, visible bucket rather than being dropped.
  return 'other_arrhythmia'
}
