export {}

declare global {
  interface Window {
    api?: {
      getBackendPort: () => Promise<number>
    }
  }
}
