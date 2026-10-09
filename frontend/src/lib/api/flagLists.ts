import { apiPost } from './http'
import type { FlagListMatchResult, FlagListParseResult } from './types'

/** Reads an FP/FN list (CSV) from disk into flagged-beat rows. */
export function parseFlagList(path: string): Promise<FlagListParseResult> {
  return apiPost('/flag-lists/parse', 'POST', { path })
}

/** Searches `folders` (recursively) for recordings named after each animal ID. */
export function matchFlagListFiles(animalIds: string[], folders: string[]): Promise<FlagListMatchResult> {
  return apiPost('/flag-lists/match', 'POST', { animal_ids: animalIds, folders })
}
