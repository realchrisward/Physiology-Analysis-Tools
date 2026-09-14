const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('api', {
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),
})
