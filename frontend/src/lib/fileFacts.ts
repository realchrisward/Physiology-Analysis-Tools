// Sorting and filtering for the file lists (the sidebar and Home). Each list
// turns its rows into `FileFacts` (what is known about a recording: name,
// folder, type, channels, beats, review counts...), then filters and sorts
// those with the user's `FileFilter`. Pure: no stores, no DOM.

import type { FileSummary } from './api/types'
import { formatBytes, formatRelativeTime } from './format'
import { matchesQuery } from './search'

export type SortKey =
  | 'added'
  | 'opened'
  | 'name'
  | 'beats'
  | 'hr'
  | 'arrhythmias'
  | 'toReview'
  | 'progress'
  | 'size'
  | 'saved'
export type SortDir = 'asc' | 'desc'
export interface SortChoice {
  key: SortKey
  dir: SortDir
}

// Where a recording's review stands. Exclusive: every recording with saved
// counts is exactly one of these.
export type ReviewGroup = 'to-review' | 'reviewed' | 'nothing-flagged' | 'not-started'
export const REVIEW_GROUPS: ReviewGroup[] = ['to-review', 'reviewed', 'nothing-flagged', 'not-started']
export const REVIEW_GROUP_LABELS: Record<ReviewGroup, string> = {
  'to-review': 'To review',
  reviewed: 'Reviewed',
  'nothing-flagged': 'Nothing flagged',
  'not-started': 'Not started',
}
export const REVIEW_GROUP_HINTS: Record<ReviewGroup, string> = {
  'to-review': 'Some flagged beats have not been looked at yet',
  reviewed: 'Every flagged beat has been looked at or decided',
  'nothing-flagged': 'Beats are detected; none is flagged as an arrhythmia',
  'not-started': 'Nothing is saved for this recording yet (it has not been opened for review)',
}

export type FacetId = 'review' | 'status' | 'channels' | 'types' | 'folders'
export const FACET_TITLES: Record<FacetId, string> = {
  review: 'Review',
  status: 'Status',
  channels: 'Channels',
  types: 'File type',
  folders: 'Folder',
}

export interface FileFacts {
  path: string
  filename: string
  // The folder the file is in (full path) and its extension (".adicht").
  folder: string
  type: string
  addedIndex: number | null
  openedAt: number | null
  // The list's own status group for the file (see each list's labels), or
  // null when not known.
  status: string | null
  beats: number | null
  meanHr: number | null
  arrhythmias: number | null
  toReview: number | null
  reviewed: number | null
  uncertain: number | null
  sizeBytes: number | null
  savedAt: number | null
  // Channel names as shown (a renamed channel by its new name). Null: not
  // known (a recording not opened since the list started remembering them).
  channels: string[] | null
  review: ReviewGroup | null
}

export interface FileFilter {
  query: string
  sort: SortChoice
  review: ReviewGroup[]
  uncertainOnly: boolean
  status: string[]
  channels: string[]
  types: string[]
  folders: string[]
}

export function emptyFacets(): Pick<FileFilter, 'review' | 'uncertainOnly' | 'status' | 'channels' | 'types' | 'folders'> {
  return { review: [], uncertainOnly: false, status: [], channels: [], types: [], folders: [] }
}

// ---- Building facts ----------------------------------------------------------

export function folderOf(path: string): string {
  const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return cut > 0 ? path.slice(0, cut) : ''
}

/** The folder's own name, for a chip ("…\\ECG_Traces" -> "ECG_Traces"). */
export function folderName(folder: string): string {
  return folder.split(/[\\/]/).filter(Boolean).pop() ?? folder
}

export function typeOf(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(dot).toLowerCase() : ''
}

export function reviewGroupOf(summary: FileSummary): ReviewGroup {
  if (!summary.found || summary.beats === 0) return 'not-started'
  if (summary.to_review > 0) return 'to-review'
  if (summary.reviewed > 0) return 'reviewed'
  return 'nothing-flagged'
}

export interface FactInput {
  path: string
  filename: string
  addedIndex?: number | null
  openedAt?: number | null
  status?: string | null
  // Live values from this session (they win over the saved counts).
  beats?: number | null
  meanHr?: number | null
  sizeBytes?: number | null
  // The recording's own channel names, when known, and their display names.
  channels?: string[] | null
  channelLabels?: Record<string, string> | null
  summary?: FileSummary | null
}

export function buildFacts(input: FactInput): FileFacts {
  const summary = input.summary ?? null
  const saved = summary?.found ? summary : null
  const labels = { ...(summary?.channel_labels ?? {}), ...(input.channelLabels ?? {}) }
  // Every channel name known to belong to the recording: its list, else the
  // ones its saved review and renames mention.
  const raw = new Set<string>(input.channels ?? [])
  if (!input.channels) {
    if (saved?.channel) raw.add(saved.channel)
    for (const channel of Object.keys(labels)) raw.add(channel)
  }
  const channels = raw.size > 0 ? [...new Set([...raw].map((channel) => labels[channel] ?? channel))] : null
  const savedAt = saved?.last_saved_at ? Date.parse(saved.last_saved_at) : NaN
  return {
    path: input.path,
    filename: input.filename,
    folder: folderOf(input.path),
    type: typeOf(input.filename),
    addedIndex: input.addedIndex ?? null,
    openedAt: input.openedAt ?? null,
    status: input.status ?? null,
    beats: input.beats ?? saved?.beats ?? null,
    meanHr: input.meanHr ?? saved?.mean_hr ?? null,
    arrhythmias: saved ? saved.arrhythmia_beats : null,
    toReview: saved ? saved.to_review : null,
    reviewed: saved ? saved.reviewed : null,
    uncertain: saved ? saved.uncertain : null,
    sizeBytes: input.sizeBytes ?? summary?.size ?? null,
    savedAt: Number.isFinite(savedAt) ? savedAt : null,
    channels,
    review: summary ? reviewGroupOf(summary) : null,
  }
}

// ---- Matching ----------------------------------------------------------------

/** The search words, against the name, the path and the channel names. */
export function matchesFileQuery(facts: FileFacts, query: string, ...extra: (string | null | undefined)[]): boolean {
  return matchesQuery(query, facts.filename, facts.path, ...(facts.channels ?? []), ...extra)
}

/**
 * Whether `facts` passes the filter's facets (not the search words). Values
 * of one facet are alternatives (any of them); different facets all apply.
 * `skip` leaves one facet out, for counting that facet's options.
 */
export function matchesFacets(facts: FileFacts, filter: FileFilter, skip?: FacetId): boolean {
  if (skip !== 'review') {
    if (filter.review.length > 0 && (facts.review === null || !filter.review.includes(facts.review))) return false
    if (filter.uncertainOnly && !((facts.uncertain ?? 0) > 0)) return false
  }
  if (skip !== 'status' && filter.status.length > 0 && (facts.status === null || !filter.status.includes(facts.status)))
    return false
  if (
    skip !== 'channels' &&
    filter.channels.length > 0 &&
    !(facts.channels ?? []).some((channel) => filter.channels.includes(channel))
  )
    return false
  if (skip !== 'types' && filter.types.length > 0 && !filter.types.includes(facts.type)) return false
  if (skip !== 'folders' && filter.folders.length > 0 && !filter.folders.includes(facts.folder)) return false
  return true
}

export function activeFacetCount(filter: FileFilter): number {
  return (
    filter.review.length +
    (filter.uncertainOnly ? 1 : 0) +
    filter.status.length +
    filter.channels.length +
    filter.types.length +
    filter.folders.length
  )
}

export function clearFacets(filter: FileFilter): void {
  Object.assign(filter, emptyFacets())
}

// Sorts and filters whose values come only from the saved counts: the list
// fetches them while one of these is in use.
const SUMMARY_SORTS = new Set<SortKey>(['beats', 'hr', 'arrhythmias', 'toReview', 'progress', 'size', 'saved'])

export function needsSummaries(filter: FileFilter): boolean {
  return (
    SUMMARY_SORTS.has(filter.sort.key) ||
    filter.review.length > 0 ||
    filter.uncertainOnly ||
    filter.status.length > 0 ||
    filter.channels.length > 0
  )
}

// ---- Facet options -----------------------------------------------------------

export interface FacetOption {
  value: string
  label: string
  count: number
  title?: string
}

function countBy(facts: FileFacts[], valuesOf: (facts: FileFacts) => string[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const item of facts) {
    for (const value of new Set(valuesOf(item))) counts.set(value, (counts.get(value) ?? 0) + 1)
  }
  return counts
}

const byText = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })

/**
 * The options of one facet over `facts` (the list after its search words),
 * each counted with the OTHER facets applied. Selected values always stay
 * listed, so they can be turned off again.
 */
export function facetOptions(
  facts: FileFacts[],
  filter: FileFilter,
  facet: FacetId,
  statusLabels: Record<string, string> = {},
): FacetOption[] {
  const pool = facts.filter((item) => matchesFacets(item, filter, facet))
  const selected = facet === 'review' ? filter.review : filter[facet]
  if (facet === 'review') {
    const counts = countBy(pool, (item) => (item.review ? [item.review] : []))
    return REVIEW_GROUPS.map((group) => ({
      value: group,
      label: REVIEW_GROUP_LABELS[group],
      count: counts.get(group) ?? 0,
      title: REVIEW_GROUP_HINTS[group],
    }))
  }
  if (facet === 'status') {
    const counts = countBy(pool, (item) => (item.status ? [item.status] : []))
    return Object.keys(statusLabels).map((value) => ({ value, label: statusLabels[value], count: counts.get(value) ?? 0 }))
  }
  const valuesOf =
    facet === 'channels'
      ? (item: FileFacts) => item.channels ?? []
      : facet === 'types'
        ? (item: FileFacts) => (item.type ? [item.type] : [])
        : (item: FileFacts) => (item.folder ? [item.folder] : [])
  const counts = countBy(pool, valuesOf)
  // Values that exist anywhere in the list (not only among the matches), so
  // an option does not vanish just because another facet hides its files.
  const values = new Set([...countBy(facts, valuesOf).keys(), ...(selected as string[])])
  return [...values]
    .map((value) => ({
      value,
      label: facet === 'folders' ? folderName(value) || value : value,
      count: counts.get(value) ?? 0,
      title: facet === 'folders' ? value : undefined,
    }))
    .sort((a, b) => byText(a.label, b.label))
}

/**
 * Whether picking one of the facet's values would narrow `facts` at all: some
 * value belongs to some files but not all. A facet that cannot (every file
 * in one folder) is left out of the panel.
 */
export function facetNarrows(facts: FileFacts[], facet: Exclude<FacetId, 'review'>): boolean {
  const valuesOf =
    facet === 'status'
      ? (item: FileFacts) => (item.status ? [item.status] : [])
      : facet === 'channels'
        ? (item: FileFacts) => item.channels ?? []
        : facet === 'types'
          ? (item: FileFacts) => (item.type ? [item.type] : [])
          : (item: FileFacts) => (item.folder ? [item.folder] : [])
  return [...countBy(facts, valuesOf).values()].some((count) => count < facts.length)
}

// ---- Sorting -----------------------------------------------------------------

export const SORT_LABELS: Record<SortKey, string> = {
  added: 'Order added',
  opened: 'Last opened',
  name: 'Name',
  beats: 'Beats',
  hr: 'Mean heart rate',
  arrhythmias: 'Arrhythmia beats',
  toReview: 'Left to review',
  progress: 'Review progress',
  size: 'File size',
  saved: 'Last saved',
}

export const DEFAULT_SORT_DIR: Record<SortKey, SortDir> = {
  added: 'asc',
  opened: 'desc',
  name: 'asc',
  beats: 'desc',
  hr: 'desc',
  arrhythmias: 'desc',
  toReview: 'desc',
  progress: 'asc',
  size: 'desc',
  saved: 'desc',
}

/** How the direction reads for `key` ("A to Z", "Newest first"...). */
export function sortDirLabel(key: SortKey, dir: SortDir): string {
  const asc = dir === 'asc'
  switch (key) {
    case 'name':
      return asc ? 'A to Z' : 'Z to A'
    case 'added':
      return asc ? 'First added first' : 'Last added first'
    case 'opened':
    case 'saved':
      return asc ? 'Oldest first' : 'Newest first'
    case 'hr':
      return asc ? 'Lowest first' : 'Highest first'
    case 'size':
      return asc ? 'Smallest first' : 'Largest first'
    case 'progress':
      return asc ? 'Least reviewed first' : 'Most reviewed first'
    default:
      return asc ? 'Fewest first' : 'Most first'
  }
}

/** Share of flagged beats looked at or decided (1 when nothing is flagged). */
function progressOf(facts: FileFacts): number | null {
  if (facts.toReview === null || facts.reviewed === null) return null
  const total = facts.toReview + facts.reviewed
  return total === 0 ? 1 : facts.reviewed / total
}

function sortValue(facts: FileFacts, key: SortKey): number | null {
  switch (key) {
    case 'added':
      return facts.addedIndex
    case 'opened':
      return facts.openedAt
    case 'beats':
      return facts.beats
    case 'hr':
      return facts.meanHr
    case 'arrhythmias':
      return facts.arrhythmias
    case 'toReview':
      return facts.toReview
    case 'progress':
      return progressOf(facts)
    case 'size':
      return facts.sizeBytes
    case 'saved':
      return facts.savedAt
    default:
      return null
  }
}

/** Sorts by `sort`; unknown values always go last, ties by name. */
export function compareFacts(a: FileFacts, b: FileFacts, sort: SortChoice): number {
  const sign = sort.dir === 'asc' ? 1 : -1
  const byName = byText(a.filename, b.filename) || byText(a.path, b.path)
  if (sort.key === 'name') return sign * byName
  const x = sortValue(a, sort.key)
  const y = sortValue(b, sort.key)
  if (x === null || y === null) {
    if (x === null && y === null) return byName
    return x === null ? 1 : -1
  }
  return x === y ? byName : sign * (x - y)
}

export function sortFacts(facts: FileFacts[], sort: SortChoice): FileFacts[] {
  return [...facts].sort((a, b) => compareFacts(a, b, sort))
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`
}

/**
 * The sorted-by value as a row shows it ("1,387 beats", "12 to review"), so
 * the order explains itself. Null for name / opened / added (the row shows
 * those anyway) and for a value not known.
 */
export function sortValueLabel(facts: FileFacts, key: SortKey, now: number = Date.now()): string | null {
  switch (key) {
    case 'beats':
      return facts.beats === null ? null : plural(facts.beats, 'beat')
    case 'hr':
      return facts.meanHr === null ? null : `${Math.round(facts.meanHr)} bpm`
    case 'arrhythmias':
      return facts.arrhythmias === null ? null : plural(facts.arrhythmias, 'arrhythmia beat')
    case 'toReview':
      return facts.toReview === null ? null : `${facts.toReview.toLocaleString()} to review`
    case 'progress': {
      const progress = progressOf(facts)
      return progress === null ? null : `${Math.floor(progress * 100)}% reviewed`
    }
    case 'size':
      return facts.sizeBytes === null ? null : formatBytes(facts.sizeBytes)
    case 'saved':
      return facts.savedAt === null ? null : `Saved ${formatRelativeTime(facts.savedAt, now)}`
    default:
      return null
  }
}

// ---- Remembering the sort ----------------------------------------------------

/** The sort saved under `storageKey`, if it is one of `allowed`; else `fallback`. */
export function readSortPref(storageKey: string, allowed: SortKey[], fallback: SortChoice): SortChoice {
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return { ...fallback }
    // Home's earlier sort setting was a bare word.
    if (raw === 'name') return { key: 'name', dir: 'asc' }
    if (raw === 'recent') return { key: 'opened', dir: 'desc' }
    const parsed = JSON.parse(raw) as Partial<SortChoice>
    if (parsed && allowed.includes(parsed.key as SortKey) && (parsed.dir === 'asc' || parsed.dir === 'desc')) {
      return { key: parsed.key as SortKey, dir: parsed.dir }
    }
  } catch {
    // Unreadable or blocked storage: the default.
  }
  return { ...fallback }
}

export function writeSortPref(storageKey: string, sort: SortChoice): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify({ key: sort.key, dir: sort.dir }))
  } catch {
    // Not remembered; nothing else depends on it.
  }
}
