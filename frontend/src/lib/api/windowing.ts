import { apiGet } from './http'
import type { BeatWindowResult, ChannelWindowResult } from './types'

export function getChannelWindow(
  path: string,
  channel: string,
  start: number,
  end: number,
  resolution: number,
): Promise<ChannelWindowResult> {
  return apiGet('/channels/window', { path, channel, start, end, resolution })
}

export function getBeatsWindow(path: string, start: number, end: number): Promise<BeatWindowResult> {
  return apiGet('/beats/window', { path, start, end })
}
