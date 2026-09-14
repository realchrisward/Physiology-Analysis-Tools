const { app, BrowserWindow, ipcMain } = require('electron')
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

app.whenReady().then(async () => {
  const { port, stop } = await startBackend()
  stopBackend = stop
  ipcMain.handle('get-backend-port', () => port)
  createWindow()
})

app.on('before-quit', async () => {
  if (stopBackend) {
    await stopBackend()
    stopBackend = null
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})
