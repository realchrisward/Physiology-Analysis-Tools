// Display helpers for report labels (an imported xlsx report shown over the
// open recording — see api/reports.ts), shared by the graph, the toolbar
// control and the beat panel so a label reads the same everywhere.

import type { ReportAgreement, ReportFilter, ReportLabel, ReportNavigationResult } from './api/types'

export interface ReportMatchStats {
  matched: number
  total: number
  percent: number
  notDetected: number
  extra: number
  // Under 90% of the report's beats have one of ours beside them: usually a
  // different channel or different detection settings.
  low: boolean
}

/**
 * How many of the report's beats line up with a beat detected here, or null
 * until our beats exist (before that nothing can match, and "0%" would only
 * look like a problem).
 */
export function reportMatch(navigation: ReportNavigationResult | null | undefined): ReportMatchStats | null {
  if (!navigation || navigation.status !== 'ok' || !navigation.has_our_beats) return null
  const total = navigation.total ?? 0
  if (total <= 0) return null
  const matched = navigation.matched ?? 0
  // Never round up to 100% while a beat is unmatched.
  const percent = matched >= total ? 100 : Math.min(99, Math.floor((matched / total) * 100))
  const notDetected = navigation.targets?.not_detected?.length ?? total - matched
  return { matched, total, percent, notDetected, extra: navigation.extra_ours ?? 0, low: matched / total < 0.9 }
}

// The comparison's agreement buckets, in the order the popover lists them.
export const REPORT_AGREEMENT_ROWS: readonly { key: ReportAgreement; name: string; title: string }[] = [
  { key: 'agree', name: 'Agree', title: 'The report and this review make the same call' },
  {
    key: 'report_only',
    name: 'Report says arrhythmia, we don’t',
    title: 'The report confirms an arrhythmia where this review says normal',
  },
  {
    key: 'ours_only',
    name: 'We say arrhythmia, report doesn’t',
    title: 'This review has an arrhythmia where the report says normal',
  },
  { key: 'pending', name: 'Not reviewed on one side', title: 'Flagged but not yet confirmed or rejected by one side' },
  { key: 'unmatched', name: 'No beat of ours there', title: 'No beat was detected here at the report beat’s time' },
  { key: 'unknown', name: 'Not compared yet', title: 'Our beats have no arrhythmia flags yet' },
]

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
