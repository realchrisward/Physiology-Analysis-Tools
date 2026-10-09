// FP/FN review projects: a model's false positive / negative list (a CSV of
// animal IDs + beat timestamps) joined to the recordings it refers to.
//
// State lives in four places:
//  - `flagProjects`: every project the technician has imported, persisted to
//    localStorage (the Home screen's "FP/FN reviews" section), most recently
//    opened first.
//  - `flagSession`: which of those are loaded into THIS session — the
//    sidebar's project tree. Like `fileRegistry`, not persisted.
//  - `flagFocus`: the flagged timestamp the graph should show. A request
//    (`requestFlagFocus`, bumps `seq`) moves the graph; a report
//    (`reportFlagFocus`, same `seq`) just records where the graph's own
//    navigation went, so the sidebar can highlight it.
//  - `flagImportDialog`: whether the import dialog is open. App.svelte renders
//    it; Home and the sidebar both open it.
//
// A project's recordings go through the same import + auto-run detection
// pipeline as any other file (see `importForProject`), so everything
// downstream — detection, arrhythmia flags, review, reports — is unchanged.

import type { FlagKind, FlagListRow } from '../api/types'
import { autoOpenRequest, fileRegistry, removeFile } from './fileRegistry.svelte'
import { importForProject } from './importQueue.svelte'

export interface FlaggedTime {
  ts: number
  kind: FlagKind
  prediction: number | null
}

export interface FlagProjectAnimal {
  animalId: string
  // Every flagged beat for this animal, ascending by ts, one per ts.
  flags: FlaggedTime[]
  // Whether the technician chose to include this animal in the review.
  selected: boolean
  // The recording found for it, or null when none has been found (yet).
  filePath: string | null
}

export interface FlagProject {
  // The CSV's path: importing the same list again replaces the project.
  id: string
  csvPath: string
  // The CSV's file name, shown as the project's name.
  name: string
  // Folders searched for recordings, in the order they were added.
  folders: string[]
  // Every animal in the CSV, in order of first appearance.
  animals: FlagProjectAnimal[]
  createdAt: number
  openedAt: number
}

export interface FlagProjectStats {
  animals: number
  selected: number
  // Selected animals with a recording found.
  found: number
  // Flagged beats across the selected animals.
  flags: number
  fp: number
  fn: number
}

const STORAGE_KEY = 'pat.flagProjects'
const MAX_PROJECTS = 20

function loadInitial(): FlagProject[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export const flagProjects: FlagProject[] = $state(loadInitial())

export const flagSession: { openIds: string[] } = $state({ openIds: [] })

export const flagFocus: { path: string | null; ts: number | null; seq: number } = $state({
  path: null,
  ts: null,
  seq: 0,
})

export const flagImportDialog: { open: boolean; csvPath: string | null; projectId: string | null } = $state({
  open: false,
  csvPath: null,
  projectId: null,
})

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(flagProjects))
  } catch {
    // localStorage unavailable or full — projects just won't survive a
    // restart; nothing else depends on this succeeding.
  }
}

/** Groups parsed CSV rows into one entry per animal (all selected, none matched yet). */
export function buildAnimals(rows: FlagListRow[]): FlagProjectAnimal[] {
  const byId = new Map<string, Map<number, FlaggedTime>>()
  for (const row of rows) {
    let flags = byId.get(row.animal_id)
    if (!flags) {
      flags = new Map()
      byId.set(row.animal_id, flags)
    }
    if (!flags.has(row.ts)) flags.set(row.ts, { ts: row.ts, kind: row.kind, prediction: row.prediction })
  }
  return Array.from(byId, ([animalId, flags]) => ({
    animalId,
    flags: Array.from(flags.values()).sort((a, b) => a.ts - b.ts),
    selected: true,
    filePath: null,
  }))
}

export function projectStats(project: FlagProject): FlagProjectStats {
  const stats: FlagProjectStats = { animals: project.animals.length, selected: 0, found: 0, flags: 0, fp: 0, fn: 0 }
  for (const animal of project.animals) {
    if (!animal.selected) continue
    stats.selected += 1
    if (animal.filePath) stats.found += 1
    stats.flags += animal.flags.length
    for (const flag of animal.flags) {
      if (flag.kind === 'fp') stats.fp += 1
      else if (flag.kind === 'fn') stats.fn += 1
    }
  }
  return stats
}

/** The selected animals that have a recording — what opening a project loads. */
export function projectRecordings(project: FlagProject): FlagProjectAnimal[] {
  return project.animals.filter((animal) => animal.selected && animal.filePath !== null)
}

export function getFlagProject(id: string): FlagProject | undefined {
  return flagProjects.find((project) => project.id === id)
}

/**
 * Adds or replaces (by id) a project, moves it to the top and persists. When
 * the project is open this session and an edit dropped some of its
 * recordings, those leave the file list too (unless another open project
 * uses them) instead of reappearing as loose files in the sidebar.
 */
export function saveFlagProject(project: FlagProject): void {
  const index = flagProjects.findIndex((p) => p.id === project.id)
  const before = index !== -1 && flagSession.openIds.includes(project.id) ? openProjectPaths() : null
  if (index !== -1) flagProjects.splice(index, 1)
  flagProjects.unshift({ ...project, openedAt: Date.now() })
  if (flagProjects.length > MAX_PROJECTS) flagProjects.length = MAX_PROJECTS
  persist()

  if (before) {
    const after = openProjectPaths()
    for (const path of before) if (!after.has(path)) removeFile(path)
  }
}

/** Drops projects from Home's list, closing any that are open this session. */
export function removeFlagProjects(ids: string[]): string[] {
  const removedPaths: string[] = []
  for (const id of ids) removedPaths.push(...closeFlagProject(id))
  const toRemove = new Set(ids)
  for (let i = flagProjects.length - 1; i >= 0; i--) {
    if (toRemove.has(flagProjects[i].id)) flagProjects.splice(i, 1)
  }
  persist()
  return removedPaths
}

export function clearFlagProjects(): string[] {
  return removeFlagProjects(flagProjects.map((project) => project.id))
}

/** Open projects this session, in the order they were opened. */
export function openFlagProjects(): FlagProject[] {
  return flagSession.openIds
    .map((id) => getFlagProject(id))
    .filter((project): project is FlagProject => project !== undefined)
}

/** Every recording path that belongs to a project open this session. */
export function openProjectPaths(): Set<string> {
  const paths = new Set<string>()
  for (const project of openFlagProjects()) {
    for (const animal of projectRecordings(project)) paths.add(animal.filePath!)
  }
  return paths
}

/** The flagged beats to highlight for a recording: every open project's flags for it, merged. */
export function flagsForPath(path: string): FlaggedTime[] {
  const byTs = new Map<number, FlaggedTime>()
  for (const project of openFlagProjects()) {
    for (const animal of projectRecordings(project)) {
      if (animal.filePath !== path) continue
      for (const flag of animal.flags) if (!byTs.has(flag.ts)) byTs.set(flag.ts, flag)
    }
  }
  return Array.from(byTs.values()).sort((a, b) => a.ts - b.ts)
}

/** The open project (and its animal) a recording belongs to, if any. */
export function projectForPath(path: string): { project: FlagProject; animal: FlagProjectAnimal } | null {
  for (const project of openFlagProjects()) {
    const animal = projectRecordings(project).find((a) => a.filePath === path)
    if (animal) return { project, animal }
  }
  return null
}

/** Asks the graph showing `path` to move to `ts`. */
export function requestFlagFocus(path: string, ts: number | null): void {
  flagFocus.path = path
  flagFocus.ts = ts
  flagFocus.seq += 1
}

/** Records that the graph's own flag navigation moved to `ts` (does not move the graph). */
export function reportFlagFocus(path: string, ts: number): void {
  flagFocus.path = path
  flagFocus.ts = ts
}

/**
 * Shows one animal's recording, centred on `ts` (default: its first flagged
 * beat). Opening the recording the graph was last focused in, without a
 * `ts`, keeps that position instead of jumping back to the first flag.
 * Returns false when the recording isn't loaded or failed to import.
 */
export function openProjectAnimal(projectId: string, animalId: string, ts?: number): boolean {
  const project = getFlagProject(projectId)
  const animal = project?.animals.find((a) => a.animalId === animalId)
  if (!project || !animal?.filePath) return false
  const row = fileRegistry.find((candidate) => candidate.path === animal.filePath)
  if (!row || row.status === 'error' || row.defaultChannel === null) return false

  if (ts !== undefined) requestFlagFocus(row.path, ts)
  else if (flagFocus.path !== row.path) requestFlagFocus(row.path, animal.flags[0]?.ts ?? null)

  autoOpenRequest.value = {
    path: row.path,
    channels: row.channels,
    defaultChannel: row.defaultChannel,
    projectId,
  }
  return true
}

/**
 * Loads a project into this session — every selected animal's recording,
 * through the normal import + detection pipeline — and shows `animalId`'s
 * recording (default: the first one that loads).
 */
export async function openFlagProject(id: string, animalId?: string): Promise<void> {
  const project = getFlagProject(id)
  if (!project) return
  project.openedAt = Date.now()
  const index = flagProjects.indexOf(project)
  if (index > 0) {
    flagProjects.splice(index, 1)
    flagProjects.unshift(project)
  }
  persist()
  if (!flagSession.openIds.includes(id)) flagSession.openIds.push(id)

  const recordings = projectRecordings(project)
  const target = recordings.find((a) => a.animalId === animalId) ?? recordings[0]
  if (!target) return
  const ordered = [target, ...recordings.filter((a) => a !== target)]
  await importForProject(ordered.map((a) => a.filePath!))

  for (const animal of ordered) {
    if (openProjectAnimal(id, animal.animalId)) return
  }
}

/**
 * Takes a project out of this session (the sidebar's X). Its recordings leave
 * the file list too, unless another open project still uses them. Like
 * removing a file, nothing on disk or in saved review work is touched.
 * Returns the paths removed from the file list.
 */
export function closeFlagProject(id: string): string[] {
  const index = flagSession.openIds.indexOf(id)
  if (index === -1) return []
  const project = getFlagProject(id)
  flagSession.openIds.splice(index, 1)
  if (!project) return []

  const stillUsed = openProjectPaths()
  const removed: string[] = []
  for (const animal of projectRecordings(project)) {
    const path = animal.filePath!
    if (stillUsed.has(path)) continue
    if (removeFile(path)) removed.push(path)
  }
  return removed
}

/** Picks a CSV and opens the import dialog for it. */
export async function startFlagListImport(): Promise<void> {
  const csvPath = (await window.api?.pickFlagList()) ?? null
  if (!csvPath) return
  flagImportDialog.csvPath = csvPath
  flagImportDialog.projectId = null
  flagImportDialog.open = true
}

/** Opens the import dialog on an existing project (e.g. to find missing recordings). */
export function editFlagProject(projectId: string): void {
  flagImportDialog.csvPath = null
  flagImportDialog.projectId = projectId
  flagImportDialog.open = true
}

export function closeFlagImportDialog(): void {
  flagImportDialog.open = false
  flagImportDialog.csvPath = null
  flagImportDialog.projectId = null
}

/** Test-only: reset everything (and clear storage) so each test starts clean. */
export function resetForTesting(): void {
  flagProjects.length = 0
  flagSession.openIds.length = 0
  flagFocus.path = null
  flagFocus.ts = null
  flagFocus.seq = 0
  closeFlagImportDialog()
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}
