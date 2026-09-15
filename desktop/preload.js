const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('api', {
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),
  pickFiles: () => ipcRenderer.invoke('pick-files'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  pickOutputDirectory: () => ipcRenderer.invoke('pick-output-directory'),
})
