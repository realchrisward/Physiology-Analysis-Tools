import { afterEach, describe, expect, it } from 'vitest'
import { fileRegistry, resetForTesting } from './fileRegistry.svelte'

afterEach(() => {
  resetForTesting()
})

describe('fileRegistry', () => {
  it('starts empty and accepts pushed rows', () => {
    expect(fileRegistry).toHaveLength(0)
    fileRegistry.push({
      path: '/data/57.txt',
      filename: '57.txt',
      status: 'ready',
      channels: ['channel 1'],
      defaultChannel: 'channel 1',
      size: 100,
      error: null,
      beatCount: null,
      meanHr: null,
    })
    expect(fileRegistry).toHaveLength(1)
    expect(fileRegistry[0].filename).toBe('57.txt')
  })

  it('resetForTesting clears the registry', () => {
    fileRegistry.push({
      path: '/data/57.txt',
      filename: '57.txt',
      status: 'ready',
      channels: [],
      defaultChannel: null,
      size: null,
      error: null,
      beatCount: null,
      meanHr: null,
    })
    resetForTesting()
    expect(fileRegistry).toHaveLength(0)
  })
})
