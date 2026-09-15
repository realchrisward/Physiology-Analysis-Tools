export {}

declare global {
  interface Window {
    api?: {
      getBackendPort: () => Promise<number>
      pickFiles: () => Promise<string[]>
      pickFolder: () => Promise<string[]>
      pickOutputDirectory: () => Promise<string | null>
    }
  }
}
