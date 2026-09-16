// Appearance preference: 'system' follows the OS (the existing
// prefers-color-scheme media query in tokens.css), 'light'/'dark' pin one
// explicitly, and 'bw' is a high-contrast black & white mode. Persisted to
// localStorage (frontend-only preference, same pattern as recentFiles.svelte.ts).

export type ThemeMode = 'system' | 'light' | 'dark' | 'bw'

const STORAGE_KEY = 'pat.theme'
const VALID_MODES: ThemeMode[] = ['system', 'light', 'dark', 'bw']

function loadInitial(): ThemeMode {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw && (VALID_MODES as string[]).includes(raw)) return raw as ThemeMode
  } catch {
    // ignore
  }
  return 'system'
}

export const themeState: { mode: ThemeMode } = $state({ mode: loadInitial() })

export function setTheme(mode: ThemeMode): void {
  themeState.mode = mode
  try {
    localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    // localStorage unavailable — the choice just won't survive a restart.
  }
}

/** Test-only: reset to the default so each test starts from a clean state. */
export function resetForTesting(): void {
  themeState.mode = 'system'
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}
