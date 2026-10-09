const { app, BrowserWindow, Menu, ipcMain, dialog } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const { startBackend } = require('./backend-process')

const DEV_SERVER_URL = 'http://localhost:5173'
const SUPPORTED_EXTENSIONS = ['adicht', 'txt', 'mat', 'gzip', 'edf']

function findSupportedFiles(dirPath) {
  const entries = fs.readdirSync(dirPath, { recursive: true })
  const matches = []
  for (const entry of entries) {
    const ext = path.extname(entry).slice(1).toLowerCase()
    if (!SUPPORTED_EXTENSIONS.includes(ext)) continue
    const fullPath = path.join(dirPath, entry)
    if (fs.statSync(fullPath).isFile()) {
      matches.push(fullPath)
    }
  }
  return matches
}

function createWindow() {
  // The stock File/Edit/View/Window menu has nothing this app needs (every
  // action lives in the UI itself). macOS keeps its menu: the app menu
  // carries Cmd+Q/Cmd+C/Cmd+V there, which don't work without it.
  if (process.platform !== 'darwin') Menu.setApplicationMenu(null)

  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // Without the menu, View > Toggle Developer Tools is gone too; keep the
  // usual shortcuts for development builds only.
  if (!app.isPackaged) {
    win.webContents.on('before-input-event', (event, input) => {
      const opensDevTools =
        input.type === 'keyDown' &&
        (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i'))
      if (!opensDevTools) return
      win.webContents.toggleDevTools()
      event.preventDefault()
    })
  }

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
    ipcMain.handle('pick-files', async () => {
      const result = await dialog.showOpenDialog({
        properties: ['openFile', 'multiSelections'],
        filters: [{ name: 'Supported files', extensions: SUPPORTED_EXTENSIONS }],
      })
      return result.canceled ? [] : result.filePaths
    })
    ipcMain.handle('pick-folder', async () => {
      const result = await dialog.showOpenDialog({
        properties: ['openDirectory'],
      })
      if (result.canceled) return []
      return findSupportedFiles(result.filePaths[0])
    })
    // The FP/FN list import: one CSV of animal IDs + timestamps.
    ipcMain.handle('pick-flag-list', async () => {
      const result = await dialog.showOpenDialog({
        properties: ['openFile'],
        filters: [
          { name: 'FP/FN lists', extensions: ['csv', 'tsv', 'txt'] },
          { name: 'All files', extensions: ['*'] },
        ],
      })
      return result.canceled || result.filePaths.length === 0 ? null : result.filePaths[0]
    })
    // A folder to search for recordings by name. Returns the folder itself,
    // not its files (unlike pick-folder) - the backend does the matching.
    ipcMain.handle('pick-directory', async () => {
      const result = await dialog.showOpenDialog({
        properties: ['openDirectory'],
      })
      return result.canceled || result.filePaths.length === 0 ? null : result.filePaths[0]
    })
    ipcMain.handle('pick-report-save-path', async (_event, defaultFileName) => {
      const result = await dialog.showSaveDialog({
        defaultPath: defaultFileName || 'report.xlsx',
        filters: [{ name: 'Excel Workbook', extensions: ['xlsx'] }],
      })
      return result.canceled || !result.filePath ? null : result.filePath
    })
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
