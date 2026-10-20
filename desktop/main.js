const { app, BrowserWindow, Menu, ipcMain, dialog } = require('electron')
const crypto = require('node:crypto')
const path = require('node:path')
const fs = require('node:fs')
const { startBackend } = require('./backend-process')

const DEV_SERVER_URL = 'http://localhost:5173'
const SUPPORTED_EXTENSIONS = ['adicht', 'txt', 'mat', 'gzip', 'edf']

// Shared secret for this launch: given to the backend (which then rejects any
// request without it) and to our own renderer over IPC. Without it, any web
// page could reach the backend's local port.
const backendToken = crypto.randomBytes(32).toString('hex')

/**
 * Every supported recording under `rootDir`, breadth-first. Async so a large
 * tree doesn't freeze the main process (and with it the window), and tolerant:
 * a folder that can't be read (EPERM, a dangling link) is skipped rather than
 * failing the whole import, and a directory is entered at most once by its
 * real path, so symlink/junction loops terminate.
 *
 * Results are pushed onto `matches` as they are found so the caller can still
 * return them if something unexpected is thrown part-way.
 */
async function findSupportedFiles(rootDir, matches = []) {
  const queue = [rootDir]
  const visited = new Set()
  for (let next = 0; next < queue.length; next++) {
    const dir = queue[next]

    let real
    try {
      real = await fs.promises.realpath(dir)
    } catch {
      continue
    }
    if (visited.has(real)) continue
    visited.add(real)

    let entries
    try {
      entries = await fs.promises.readdir(dir, { withFileTypes: true })
    } catch {
      continue
    }

    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name)
      let isDir = entry.isDirectory()
      let isFile = entry.isFile()
      if (!isDir && !isFile) {
        // A symlink / junction / unknown type: look at what it points to.
        try {
          const stats = await fs.promises.stat(fullPath)
          isDir = stats.isDirectory()
          isFile = stats.isFile()
        } catch {
          continue
        }
      }

      if (isDir) {
        queue.push(fullPath)
      } else if (isFile) {
        const ext = path.extname(entry.name).slice(1).toLowerCase()
        if (SUPPORTED_EXTENSIONS.includes(ext)) matches.push(fullPath)
      }
    }
  }
  return matches
}

// Dialogs get the asking window as their parent so they are modal: while one
// is open the window can't be clicked, so a double-click can't stack a second
// picker on top of the first.
function showOpenDialog(event, options) {
  const win = BrowserWindow.fromWebContents(event.sender)
  return win ? dialog.showOpenDialog(win, options) : dialog.showOpenDialog(options)
}

function showSaveDialog(event, options) {
  const win = BrowserWindow.fromWebContents(event.sender)
  return win ? dialog.showSaveDialog(win, options) : dialog.showSaveDialog(options)
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

// The backend died on its own (crash, OOM-kill) after a successful start.
// Without this the window just fills with "Failed to fetch".
function handleBackendExit({ code, signal, stderrTail }) {
  if (quitting) return
  backendReady = false
  stopBackend = null

  const status = signal ? `signal ${signal}` : `exit code ${code}`
  const tail = (stderrTail || '').trim().slice(-1500)
  const detail =
    `The backend process ended (${status}). Restart the app to continue.` +
    (tail ? `\n\nLast output:\n${tail}` : '')
  const options = {
    type: 'error',
    title: 'Physiology Analysis Tools',
    message: 'The analysis backend stopped unexpectedly',
    detail,
    buttons: ['Restart', 'Quit'],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  }
  const parent = BrowserWindow.getAllWindows()[0]
  const shown = parent ? dialog.showMessageBox(parent, options) : dialog.showMessageBox(options)
  shown.then(({ response }) => {
    quitting = true
    if (response === 0) {
      // Release the single-instance lock first so the relaunched copy can take it.
      app.releaseSingleInstanceLock()
      app.relaunch()
      app.exit(0)
    } else {
      app.quit()
    }
  })
}

function focusExistingWindow() {
  const win = BrowserWindow.getAllWindows()[0]
  if (!win) return
  if (win.isMinimized()) win.restore()
  win.focus()
}

async function startApp() {
  try {
    const { port, stop } = await startBackend({ token: backendToken, onExit: handleBackendExit })
    if (quitting) {
      await stop()
      return
    }
    stopBackend = stop
    backendReady = true
    ipcMain.handle('get-backend-port', () => port)
    ipcMain.handle('get-backend-token', () => backendToken)
    ipcMain.handle('pick-files', async (event) => {
      const result = await showOpenDialog(event, {
        properties: ['openFile', 'multiSelections'],
        filters: [{ name: 'Supported files', extensions: SUPPORTED_EXTENSIONS }],
      })
      return result.canceled ? [] : result.filePaths
    })
    ipcMain.handle('pick-folder', async (event) => {
      // Never rejects: an unreadable subfolder (or anything else unexpected)
      // yields the recordings found so far instead of an IPC error.
      const found = []
      try {
        const result = await showOpenDialog(event, {
          properties: ['openDirectory'],
        })
        if (result.canceled || result.filePaths.length === 0) return []
        await findSupportedFiles(result.filePaths[0], found)
      } catch (err) {
        console.error('[pick-folder]', err)
      }
      return found
    })
    // A beat-label report (xlsx) to show over the open recording. Opens in
    // the folder the last report came from, when the renderer knows it.
    ipcMain.handle('pick-report', async (event, defaultDir) => {
      const result = await showOpenDialog(event, {
        properties: ['openFile'],
        defaultPath: typeof defaultDir === 'string' && defaultDir ? defaultDir : undefined,
        filters: [
          { name: 'Excel reports', extensions: ['xlsx', 'xlsm'] },
          { name: 'All files', extensions: ['*'] },
        ],
      })
      return result.canceled || result.filePaths.length === 0 ? null : result.filePaths[0]
    })
    // The FP/FN list import: one CSV of animal IDs + timestamps.
    ipcMain.handle('pick-flag-list', async (event) => {
      const result = await showOpenDialog(event, {
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
    ipcMain.handle('pick-directory', async (event) => {
      const result = await showOpenDialog(event, {
        properties: ['openDirectory'],
      })
      return result.canceled || result.filePaths.length === 0 ? null : result.filePaths[0]
    })
    ipcMain.handle('pick-report-save-path', async (event, defaultFileName) => {
      const result = await showSaveDialog(event, {
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
}

// Two copies would share one SQLite database and double the memory; a second
// launch just brings the first window forward.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', focusExistingWindow)
  app.whenReady().then(startApp)
}

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
