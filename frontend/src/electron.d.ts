export {}

declare global {
  interface Window {
    api?: {
      getBackendPort: () => Promise<number>
      pickFiles: () => Promise<string[]>
      pickFolder: () => Promise<string[]>
      pickFlagList: () => Promise<string | null>
      pickDirectory: () => Promise<string | null>
      pickReportSavePath: (defaultFileName?: string) => Promise<string | null>
    }
  }
}
