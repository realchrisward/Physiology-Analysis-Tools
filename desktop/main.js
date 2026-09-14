const { app, BrowserWindow, ipcMain, dialog } = require('electron')
const path = require('node:path')
const { startBackend } = require('./backend-process')

const DEV_SERVER_URL = 'http://localhost:5173'

function createWindow() {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  win.webContents.on('will-navigate', (event, url) => {
    if (url !== win.webContents.getURL()) {
      event.preventDefault()
    }
  })

  if (app.isPackaged) {
    win.loadFile(path.join(__dirname, '..', 'frontend', 'dist', 'index.html'))
  } else {
    win.loadURL(DEV_SERVER_URL)
  }
}

let stopBackend = null
let backendReady = false
let quitting = false

app.whenReady().then(async () => {
  try {
    const { port, stop } = await startBackend()
    if (quitting) {
      await stop()
      return
    }
    stopBackend = stop
    backendReady = true
    ipcMain.handle('get-backend-port', () => port)
    createWindow()
  } catch (err) {
    dialog.showErrorBox('Failed to start backend', err.message || String(err))
    app.quit()
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', (event) => {
  if (quitting) return
  quitting = true
  if (!stopBackend) return
  event.preventDefault()
  const stop = stopBackend
  stopBackend = null
  stop().finally(() => app.quit())
})

app.on('activate', () => {
  if (backendReady && BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})
