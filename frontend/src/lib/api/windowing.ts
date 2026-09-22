import { apiGet } from './http'
import type { BeatsOfInterestResult, BeatWindowResult, ChannelWindowResult } from './types'

export function getChannelWindow(
  path: string,
  channel: string,
  start: number,
  end: number,
  resolution: number,
  filtered: boolean = false,
): Promise<ChannelWindowResult> {
  return apiGet('/channels/window', { path, channel, start, end, resolution, filtered })
}

export function getBeatsWindow(path: string, start: number, end: number): Promise<BeatWindowResult> {
  return apiGet('/beats/window', { path, start, end })
}

// Every flagged beat's timestamp across the WHOLE file, not just the visible
// window — the graph's beat-of-interest navigation steps through the entire
// recording, so it can't be driven by the viewport-scoped beats window.
export function getBeatsOfInterest(path: string): Promise<BeatsOfInterestResult> {
  return apiGet('/beats/of-interest', { path })
}
