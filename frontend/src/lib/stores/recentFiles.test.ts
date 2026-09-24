import { afterEach, describe, expect, it } from 'vitest'
import { recentFiles, recordRecentFile, resetForTesting } from './recentFiles.svelte'

afterEach(() => {
  resetForTesting()
})

describe('recentFiles', () => {
  it('records a new file at the front of the list', () => {
    recordRecentFile('/data/a.txt', 'a.txt')
    expect(recentFiles).toHaveLength(1)
    expect(recentFiles[0].path).toBe('/data/a.txt')
  })

  it('bumps an already-recorded path to the front instead of duplicating it', () => {
    recordRecentFile('/data/a.txt', 'a.txt')
    recordRecentFile('/data/b.txt', 'b.txt')
    recordRecentFile('/data/a.txt', 'a.txt')

    expect(recentFiles).toHaveLength(2)
    expect(recentFiles[0].path).toBe('/data/a.txt')
    expect(recentFiles[1].path).toBe('/data/b.txt')
  })

  it('caps the list at 30 entries, dropping the oldest', () => {
    for (let i = 0; i < 32; i++) {
      recordRecentFile(`/data/${i}.txt`, `${i}.txt`)
    }
    expect(recentFiles).toHaveLength(30)
    expect(recentFiles[0].path).toBe('/data/31.txt')
    expect(recentFiles.find((f) => f.path === '/data/0.txt')).toBeUndefined()
  })

  it('persists to localStorage and survives being reloaded', () => {
    recordRecentFile('/data/a.txt', 'a.txt')
    const raw = localStorage.getItem('pat.recentFiles')
    expect(raw).toBeTruthy()
    const parsed = JSON.parse(raw!)
    expect(parsed[0].path).toBe('/data/a.txt')
  })
})
