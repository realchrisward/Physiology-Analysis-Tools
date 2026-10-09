const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('api', {
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),
  pickFiles: () => ipcRenderer.invoke('pick-files'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  pickFlagList: () => ipcRenderer.invoke('pick-flag-list'),
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  pickReportSavePath: (defaultFileName) => ipcRenderer.invoke('pick-report-save-path', defaultFileName),
})
