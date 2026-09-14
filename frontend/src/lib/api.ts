export async function getBackendUrl(): Promise<string> {
  if (typeof window !== 'undefined' && window.api?.getBackendPort) {
    const port = await window.api.getBackendPort()
    return `http://127.0.0.1:${port}`
  }
  return 'http://127.0.0.1:8000'
}
