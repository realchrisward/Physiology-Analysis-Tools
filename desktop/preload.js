const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('api', {
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),
  getBackendToken: () => ipcRenderer.invoke('get-backend-token'),
  pickFiles: () => ipcRenderer.invoke('pick-files'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  pickFlagList: () => ipcRenderer.invoke('pick-flag-list'),
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  pickReport: (defaultDir) => ipcRenderer.invoke('pick-report', defaultDir),
  // The on-disk path of a file dropped onto the window (File.path no longer
  // exists with context isolation). '' for files that aren't on disk.
  getPathForFile: (file) => webUtils.getPathForFile(file),
  pickReportSavePath: (defaultFileName) => ipcRenderer.invoke('pick-report-save-path', defaultFileName),
})
