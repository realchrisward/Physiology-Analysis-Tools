import { getBackendToken, getBackendUrl, hasBackendTokenSource } from '../api'

type HttpMethod = 'POST' | 'PUT' | 'PATCH' | 'DELETE'

function buildQuery(params?: Record<string, string | number | boolean>): string {
  if (!params) return ''
  const entries = Object.entries(params)
  if (entries.length === 0) return ''
  const query = entries
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&')
  return `?${query}`
}

const TOKEN_HEADER = 'X-PAT-Token'

function errorResult<T>(message: string): T {
  return { status: 'error', error: message } as T
}

/**
 * GET a JSON endpoint from the backend. Never throws: a network failure or a
 * non-2xx HTTP response is converted into `{status: 'error', error: <message>}`,
 * which every backend response type already supports as a shape.
 */
export async function apiGet<T>(
  path: string,
  query?: Record<string, string | number | boolean>,
): Promise<T> {
  const backendUrl = await getBackendUrl()
  const url = `${backendUrl}${path}${buildQuery(query)}`
  // The Electron backend requires its per-launch token on every request.
  // Elsewhere there is nothing to look up, so don't await anything extra.
  const token = hasBackendTokenSource() ? await getBackendToken() : null

  try {
    const response = token
      ? await fetch(url, { headers: { [TOKEN_HEADER]: token } })
      : await fetch(url)
    if (!response.ok) {
      return errorResult<T>(`HTTP ${response.status}`)
    }
    return (await response.json()) as T
  } catch (err) {
    return errorResult<T>(err instanceof Error ? err.message : String(err))
  }
}

/**
 * POST/PUT/PATCH/DELETE a JSON body to the backend. Never throws: same clean
 * error contract as `apiGet`.
 */
export async function apiPost<T>(path: string, method: HttpMethod, body?: unknown): Promise<T> {
  const backendUrl = await getBackendUrl()
  const url = `${backendUrl}${path}`
  const token = hasBackendTokenSource() ? await getBackendToken() : null

  try {
    const response = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', ...(token ? { [TOKEN_HEADER]: token } : {}) },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      return errorResult<T>(`HTTP ${response.status}`)
    }
    return (await response.json()) as T
  } catch (err) {
    return errorResult<T>(err instanceof Error ? err.message : String(err))
  }
}
