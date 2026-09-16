// Shared, cross-component registry of every file imported this session.
//
// Svelte 5 runes-in-module pattern: a `$state` array declared here is
// reactive for every component that imports it, with no prop drilling.
// `ImportScreen` is still the only thing that ever pushes into or mutates
// this array (its import/detect orchestration is unchanged); `Sidebar` is a
// pure read-plus-click consumer of the same live objects.

export interface FileRow {
  path: string
  filename: string
  status: 'ready' | 'error' | 'detecting' | 'detected' | 'detection-error'
  channels: string[]
  defaultChannel: string | null
  size: number | null
  error: string | null
  beatCount: number | null
  meanHr: number | null
}

export const fileRegistry: FileRow[] = $state([])

/** Test-only: reset the registry so each test starts from a clean state. */
export function resetForTesting(): void {
  fileRegistry.length = 0
}
