// Types mirroring backend/models.py exactly (field names, optionality).
// `| null` corresponds to Pydantic's `| None`.

// Synthesized by apiGet/apiPost (see http.ts) on network failure or a non-2xx
// HTTP response. Most backend response types already include `status`/`error`
// fields, so that shape overlaps harmlessly with a successful response's own
// error state. But a handful of response types don't carry `status`/`error`
// at all (or aren't an object), so their return type must be unioned with
// this explicitly to force callers to narrow before accessing success fields.
export interface ApiError {
  status: 'error'
  error: string
}

export interface FileImportResult {
  path: string
  filename: string
  status: string
  channels: string[]
  time_column: string | null
  size: number | null
  modified_time: number | null
  default_channel: string | null
  default_channel_matched_rule: boolean
  error: string | null
}

export interface ImportResponse {
  results: FileImportResult[]
}

export interface Beat {
  ts: number
  rr: number
  r_amplitude: number
  hr: number
}

export interface BeatDetectionResult {
  status: string
  beats: Beat[]
  count: number
  mean_hr: number | null
  duration: number | null
  elapsed_seconds: number
  file_size_bytes: number | null
  error: string | null
}

export interface BeatSettingsModel {
  min_RR: number
  ecg_invert: boolean
  auto_detect_invert: boolean
  ecg_filter: boolean
  ecg_filt_order: number
  ecg_filt_cutoff: number
  abs_thresh: number | null
  perc_thresh: number | null
}

export interface ArrhythmiaSettingsModel {
  bradycardia_absolute_hr: number
  tachycardia_absolute_hr: number
  skipped_beat_multiple_rr: number
  premature_beat_multiple_rr: number
  beat_window: number
  beat_window_bias: number
  beat_length: number
  kde_bandwidth: number
  min_rr: number
  max_rr: number
  eps_auto: boolean
  eps_percentile: number
  eps: number
  min_samples: number
}

export interface SettingsPayload {
  beat: BeatSettingsModel
  arrhythmia: ArrhythmiaSettingsModel
}

export interface SettingsResult {
  status: string
  settings: SettingsPayload | null
  error: string | null
}

export interface DatabaseResetResult {
  status: string
  error: string | null
}

export interface DatabaseInfoResult {
  status: string
  path: string | null
  directory: string | null
  size_bytes: number | null
  file_count: number | null
  beat_count: number | null
  error: string | null
}

export interface ArrhythmiaBeat {
  ts: number
  bradycardia_absolute: boolean | null
  tachycardia_absolute: boolean | null
  skipped_beat: boolean | null
  prem_beat: boolean | null
  abn_cluster: boolean | null
  any_arrhythmia: boolean
  other_arrhythmia: boolean
}

export interface ArrhythmiaDetectionResult {
  status: string
  beats: ArrhythmiaBeat[]
  count: number
  any_arrhythmia_count: number
  elapsed_seconds: number
  error: string | null
}

export interface ChannelWindowResult {
  status: string
  x: number[]
  y: number[]
  point_count: number
  downsampled: boolean
  error: string | null
}

export interface WindowBeat {
  ts: number
  rr: number
  r_amplitude: number
  hr: number
  bradycardia_absolute: boolean | null
  tachycardia_absolute: boolean | null
  skipped_beat: boolean | null
  prem_beat: boolean | null
  abn_cluster: boolean | null
  any_arrhythmia: boolean | null
  other_arrhythmia: boolean | null
  review_state: string
  reassigned_category: string | null
  // ISO time the technician added this beat by hand; absent for detected beats.
  manual_added_at?: string | null
}

export interface BeatWindowResult {
  status: string
  beats: WindowBeat[]
  count: number
  error: string | null
}

export interface BeatDeleteResult {
  status: string
  error: string | null
}

export interface DiscardStateResult {
  status: string
  error: string | null
}

export interface BeatsOfInterestResult {
  status: string
  ts: number[]
  count: number
  // Typical beat-to-beat interval in seconds (dominant RR mode), or null when
  // there are too few beats to tell. Sizes the focus view around a beat.
  typical_rr?: number | null
  error: string | null
}

export interface PersistedBeat {
  ts: number
  rr: number
  r_amplitude: number
  hr: number
  manual_added_at?: string | null
  bradycardia_absolute: boolean | null
  tachycardia_absolute: boolean | null
  skipped_beat: boolean | null
  prem_beat: boolean | null
  abn_cluster: boolean | null
  any_arrhythmia: boolean | null
  other_arrhythmia: boolean | null
  review_state: string
  reassigned_category: string | null
}

export interface BadDataMark {
  id: number
  start: number
  stop: number
}

export interface FileStateResult {
  status: string
  found: boolean
  channel: string | null
  beats: PersistedBeat[]
  bad_data_marks: BadDataMark[]
  beat_settings: BeatSettingsModel | null
  arrhythmia_settings: ArrhythmiaSettingsModel | null
  error: string | null
}

export interface ChannelPersistResult {
  status: string
  channel: string | null
  error: string | null
}

export interface PersistBeatsResult {
  status: string
  count: number
  error: string | null
}

export interface BadDataAddResult {
  status: string
  mark: BadDataMark | null
  error: string | null
}

export interface BadDataUpdateResult {
  status: string
  mark: BadDataMark | null
  error: string | null
}

export interface BeatSnapResult {
  status: string
  ts: number | null
  error: string | null
}

export interface BeatAddResult {
  status: string
  beat: WindowBeat | null
  error: string | null
}

export interface BadDataDeleteResult {
  status: string
  error: string | null
}

export interface CategoryUpdateResult {
  status: string
  ts: number | null
  review_state: string | null
  reassigned_category: string | null
  error: string | null
}

export interface ReportResult {
  status: string
  output_path: string | null
  error: string | null
}

// FP/FN lists: a model's false positives / negatives, one row per flagged
// beat, keyed by animal ID (= a recording's file name without extension)
// and the beat's timestamp in seconds. See backend/flag_lists.py.
//   'fp'   - the model called it an arrhythmia, the annotators did not.
//   'fn'   - the annotators called it an arrhythmia, the model did not.
//   'flag' - the list didn't say which (no ground_truth/prediction/kind column).
export type FlagKind = 'fp' | 'fn' | 'flag'

export interface FlagListRow {
  animal_id: string
  ts: number
  kind: FlagKind
  prediction: number | null
  ground_truth: number | null
}

export interface FlagListParseResult {
  status: 'ok' | 'error'
  error?: string | null
  filename?: string
  rows?: FlagListRow[]
  total_rows?: number
  // Rows dropped because their animal ID or timestamp was blank/unreadable.
  skipped_rows?: number
}

export interface FlagListMatch {
  animal_id: string
  // Every recording whose file name (minus extension) equals the animal ID,
  // case-insensitively, .adicht first. Usually exactly one.
  paths: string[]
}

export interface FlagListMatchResult {
  status: 'ok' | 'error'
  error?: string | null
  matches?: FlagListMatch[]
  // Requested animal IDs with no matching recording in any folder.
  missing?: string[]
  // Requested folders that don't exist / aren't readable.
  missing_folders?: string[]
  scanned_files?: number
  // Sub-folders that could not be read, so recordings in them can't have been found.
  skipped_dirs?: number
  // The scan stopped at its file/time limit before covering every folder.
  truncated?: boolean
}

export interface EvictResult {
  status: string
  evicted?: number
  error?: string | null
}
