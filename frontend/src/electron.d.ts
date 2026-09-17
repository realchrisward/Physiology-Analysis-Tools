export {}

declare global {
  interface Window {
    api?: {
      getBackendPort: () => Promise<number>
      pickFiles: () => Promise<string[]>
      pickFolder: () => Promise<string[]>
      pickReportSavePath: (defaultFileName?: string) => Promise<string | null>
    }
  }
}
