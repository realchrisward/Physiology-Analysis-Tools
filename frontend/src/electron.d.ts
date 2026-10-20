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
      // A beat-label report (.xlsx) to show over the open recording.
      pickReport?: (defaultDir?: string) => Promise<string | null>
      // The on-disk path of a dropped File ('' when it has none).
      getPathForFile?: (file: File) => string
      pickReportSavePath: (defaultFileName?: string) => Promise<string | null>
    }
  }
}
