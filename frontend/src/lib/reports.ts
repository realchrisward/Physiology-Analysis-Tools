// Display helpers for report labels (an imported xlsx report shown over the
// open recording — see api/reports.ts), shared by the graph, the toolbar
// control and the beat panel so a label reads the same everywhere.

import type { ReportAgreement, ReportFilter, ReportLabel } from './api/types'

export const REPORT_LABELS: readonly ReportLabel[] = [2, 1, 0, -1]

export const REPORT_LABEL_NAME: Record<ReportLabel, string> = {
  2: 'Confirmed arrhythmia',
  1: 'Detected, not reviewed',
  0: 'Normal',
  [-1]: 'Rejected',
}

export const REPORT_LABEL_TITLE: Record<ReportLabel, string> = {
  2: 'Label 2 — confirmed arrhythmia (label = arr)',
  1: 'Label 1 — detected but neither confirmed nor rejected (excluded from training)',
  0: 'Label 0 — not detected and not reviewed (label = normal)',
  [-1]: 'Label -1 — detected, then rejected (label = normal)',
}

// CSS custom properties (tokens.css) for each label's marker color.
export const REPORT_LABEL_COLOR_VAR: Record<ReportLabel, string> = {
  2: '--color-report-arr',
  1: '--color-report-pending',
  0: '--color-report-normal',
  [-1]: '--color-report-rejected',
}

export const REPORT_FILTERS: readonly ReportFilter[] = [
  'arrhythmia',
  'unreviewed',
  'rejected',
  'disagreements',
  'not_detected',
]

export const REPORT_FILTER_NAME: Record<ReportFilter, string> = {
  arrhythmia: 'Arrhythmia',
  unreviewed: 'Not reviewed',
  rejected: 'Rejected',
  disagreements: 'Disagreements',
  not_detected: 'Not detected by us',
}

export const REPORT_FILTER_TITLE: Record<ReportFilter, string> = {
  arrhythmia: 'Beats the report labels as confirmed arrhythmia (2)',
  unreviewed: 'Beats the report detected but never confirmed or rejected (1)',
  rejected: 'Beats the report detected, then rejected (-1)',
  disagreements: 'Beats where the report and this review disagree about arrhythmia',
  not_detected: 'Report beats with no beat detected here (different channel or detection settings?)',
}

export const REPORT_AGREEMENT_TEXT: Record<ReportAgreement, string> = {
  agree: 'Agrees with this review',
  report_only: 'Report: arrhythmia — this review: normal',
  ours_only: 'This review: arrhythmia — report: normal',
  pending: 'Not reviewed on one side yet',
  unmatched: 'No beat detected here in this recording',
  unknown: 'Beats not detected yet',
}

/** "prem_beat" → "premature beat", for listing which detectors flagged a beat. */
export function reportCategoryName(category: string): string {
  const names: Record<string, string> = {
    bradycardia_absolute: 'bradycardia',
    tachycardia_absolute: 'tachycardia',
    skipped_beat: 'skipped beat',
    prem_beat: 'premature beat',
    abn_cluster: 'abnormal shape',
    other_arrhythmia: 'other',
  }
  return names[category] ?? category.replace(/_/g, ' ')
}
