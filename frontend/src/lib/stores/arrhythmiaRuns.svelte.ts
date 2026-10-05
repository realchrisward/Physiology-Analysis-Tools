// Which arrhythmia detection methods have produced results for a file, kept
// for the app session so the Run buttons' green "done" state survives
// switching to another file and back (ReviewWorkspace is remounted per file,
// so it can't hold this itself).
//
// Deliberately in-memory only: the saved review in SQLite doesn't record
// which method made its flags, and after "Delete database" (which reloads the
// window) a persisted copy of this would claim results that no longer exist.

import type { ArrhythmiaMethod } from '../api/arrhythmia'

// One entry per file. The backend keeps a single beat set per file (the last
// channel detected), so results only ever belong to ONE channel at a time -
// hence the channel is stored alongside, not used as a second key.
interface FileRuns {
  channel: string
  methods: ArrhythmiaMethod[]
}

const runs: Record<string, FileRuns> = $state({})

/** Methods already run for `path` on `channel` ([] if none, or for another channel). */
export function getArrhythmiaRuns(path: string, channel: string): ArrhythmiaMethod[] {
  const entry = runs[path]
  return entry && entry.channel === channel ? entry.methods : []
}

export function markArrhythmiaRan(path: string, channel: string, ...methods: ArrhythmiaMethod[]): void {
  const entry = runs[path]
  const existing = entry && entry.channel === channel ? entry.methods : []
  runs[path] = { channel, methods: [...new Set([...existing, ...methods])] }
}

/** Forgets a file's results: its beats were re-detected, switched channel, or discarded. */
export function clearArrhythmiaRuns(path: string): void {
  delete runs[path]
}

/** Test-only: reset so each test starts clean. */
export function resetForTesting(): void {
  for (const path of Object.keys(runs)) delete runs[path]
}
