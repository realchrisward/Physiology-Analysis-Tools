// Display helpers for FP/FN review flags (see stores/flagProjects.svelte.ts),
// shared by the Home screen, the sidebar and the graph so a flag reads the
// same everywhere.

import type { FlagKind } from './api/types'

export const FLAG_KIND_LABEL: Record<FlagKind, string> = {
  fp: 'FP',
  fn: 'FN',
  flag: 'Flag',
}

export const FLAG_KIND_TITLE: Record<FlagKind, string> = {
  fp: 'False positive — the model called this beat an arrhythmia, the annotators did not',
  fn: 'False negative — the annotators called this beat an arrhythmia, the model did not',
  flag: 'Flagged in the list',
}

// CSS custom properties (tokens.css) for each kind's color and soft fill.
export const FLAG_KIND_COLOR_VAR: Record<FlagKind, string> = {
  fp: '--color-flag-fp',
  fn: '--color-flag-fn',
  flag: '--color-flag',
}

export const FLAG_KIND_SOFT_VAR: Record<FlagKind, string> = {
  fp: '--color-flag-fp-soft',
  fn: '--color-flag-fn-soft',
  flag: '--color-flag-soft',
}

/**
 * A flag's position in its recording: "44.084 s" under a minute, then
 * "1:54.573" and "1:02:03.500" — millisecond precision, since a flag marks
 * one beat and a mouse's beats are ~100 ms apart.
 */
export function formatFlagTime(ts: number): string {
  if (!Number.isFinite(ts)) return '—'
  if (ts < 60) return `${ts.toFixed(3)} s`
  const totalMs = Math.round(ts * 1000)
  const hours = Math.floor(totalMs / 3_600_000)
  const minutes = Math.floor((totalMs % 3_600_000) / 60_000)
  const seconds = ((totalMs % 60_000) / 1000).toFixed(3).padStart(6, '0')
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}` : `${minutes}:${seconds}`
}

/** The model's score, e.g. "0.999", or null when the list had none. */
export function formatPrediction(prediction: number | null): string | null {
  return prediction === null || !Number.isFinite(prediction) ? null : prediction.toFixed(3)
}
