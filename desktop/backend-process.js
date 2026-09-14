const { spawn } = require('node:child_process')
const net = require('node:net')
const path = require('node:path')

const REPO_ROOT = path.join(__dirname, '..')
const HEALTH_TIMEOUT_MS = 8000
const POLL_INTERVAL_MS = 200

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port
      server.close(() => resolve(port))
    })
    server.on('error', reject)
  })
}

async function waitForHealth(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`)
      if (res.ok) return
    } catch {
      // backend not listening yet
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
  }
  throw new Error(`Backend did not become healthy within ${timeoutMs}ms on port ${port}`)
}

async function startBackend() {
  const port = await findFreePort()
  const pythonBin = path.join(REPO_ROOT, '.venv', 'bin', 'python')

  const proc = spawn(
    pythonBin,
    ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: REPO_ROOT, stdio: 'pipe' },
  )

  // Track spawn errors and exit state at spawn time
  let spawnError = null
  proc.on('error', (err) => {
    spawnError = spawnError || err
    console.error('[backend] process error:', err)
  })

  let exited = false
  proc.once('exit', () => {
    exited = true
  })

  proc.stdout.on('data', (d) => console.log('[backend]', d.toString().trim()))
  proc.stderr.on('data', (d) => console.log('[backend]', d.toString().trim()))

  try {
    await waitForHealth(port, HEALTH_TIMEOUT_MS)
  } catch (err) {
    // Kill the process if it's still running before throwing
    if (!exited) {
      proc.kill('SIGTERM')
    }
    throw spawnError || err
  }

  const stop = () => {
    // If already exited, resolve immediately
    if (exited) {
      return Promise.resolve()
    }
    // Otherwise, attach listener and kill
    return new Promise((resolve) => {
      proc.once('exit', () => resolve())
      proc.kill('SIGTERM')
    })
  }

  return { port, stop }
}

module.exports = { startBackend }
