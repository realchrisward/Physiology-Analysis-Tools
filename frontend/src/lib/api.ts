export async function getBackendUrl(): Promise<string> {
  if (typeof window !== 'undefined' && window.api?.getBackendPort) {
    const port = await window.api.getBackendPort()
    return `http://127.0.0.1:${port}`
  }
  return 'http://127.0.0.1:8000'
}

let cachedToken: string | null = null

/**
 * Whether this environment can have a backend token at all (Electron's preload
 * exposes the lookup). Callers use it to skip the async lookup entirely where
 * there is none, so a web build or a test makes exactly the same sequence of
 * awaits per request as before the token existed.
 */
export function hasBackendTokenSource(): boolean {
  return typeof window !== 'undefined' && !!window.api?.getBackendToken
}

/**
 * The per-launch secret the Electron backend requires on every request
 * (header `X-PAT-Token`). Fetched over IPC once and cached. Null when there is
 * none: the web build, a dev backend started by hand, or tests - those talk to
 * a backend that doesn't check. A missing or failed lookup is not cached, so a
 * later call can still pick the token up.
 */
export async function getBackendToken(): Promise<string | null> {
  if (cachedToken) return cachedToken
  if (typeof window === 'undefined' || !window.api?.getBackendToken) return null
  try {
    const token = await window.api.getBackendToken()
    if (token) cachedToken = token
    return token || null
  } catch {
    return null
  }
}
