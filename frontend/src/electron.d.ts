export {}

declare global {
  interface Window {
    api?: {
      getBackendPort: () => Promise<number>
      // Per-launch secret the backend requires on every request (see backend/app.py).
      getBackendToken?: () => Promise<string | null>
      pickFiles: () => Promise<string[]>
      pickFolder: () => Promise<string[]>
      pickFlagList: () => Promise<string | null>
      pickDirectory: () => Promise<string | null>
      pickReportSavePath: (defaultFileName?: string) => Promise<string | null>
    }
  }
}
