const { spawn } = require('node:child_process')
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')

const REPO_ROOT = path.join(__dirname, '..')
// uvicorn only logs anything once backend.app has finished importing
// (scipy/pandas/numpy, fastapi) — so on a cold environment nothing appears
// in stderr for the whole time it's importing. On Windows that import can
// be considerably slower than it is here: real-time antivirus scanning
// each newly-installed native extension (.pyd) the first time it loads is
// a known, common cause, and can on its own push a cold start past 8s even
// though the backend is starting fine. Overridable via an env var so a
// slow machine can be tuned without a code change.
const HEALTH_TIMEOUT_MS = Number(process.env.PAT_BACKEND_HEALTH_TIMEOUT_MS) || 30000
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

async function waitForHealth(port, timeoutMs, state) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (state.exited) {
      throw new Error(
        `Backend exited (code=${state.exitInfo.code}, signal=${state.exitInfo.signal}) before becoming healthy:\n${state.stderrTail}`,
      )
    }
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`)
      if (res.ok) {
        const body = await res.json()
        if (body.status === 'ok') return
      }
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
  }
  const hint = state.stderrTail
    ? state.stderrTail
    : 'No output was received from the backend process in this time. This usually means it is still ' +
      'starting — a cold virtual environment, or antivirus scanning newly-installed packages, can take ' +
      'longer than usual on the very first run. Try again; if it keeps happening, open a terminal in the ' +
      'repo root and run the backend directly (Windows: ".venv\\Scripts\\python -m uvicorn backend.app:app ' +
      '--port 8001", macOS/Linux: ".venv/bin/python -m uvicorn backend.app:app --port 8001") to see what it ' +
      'is doing.'
  throw new Error(`Backend did not become healthy within ${timeoutMs}ms on port ${port}:\n${hint}`)
}

async function startBackend() {
  const port = await findFreePort()
  // A virtualenv puts its interpreter in a different place on Windows than
  // it does elsewhere, so hardcoding one layout makes `npm run dev` fail on
  // the other platform before the backend can even start.
  const pythonBin =
    process.platform === 'win32'
      ? path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : path.join(REPO_ROOT, '.venv', 'bin', 'python')

  // A missing venv would otherwise spawn, fail silently in a way that still
  // only surfaces as the generic health-check timeout above, and leave the
  // technician no wiser about what to fix. Caught here instead, with the
  // one concrete, actionable cause.
  if (!fs.existsSync(pythonBin)) {
    throw new Error(
      `Python virtual environment not found at ${pythonBin}. Follow the setup steps in instructions.md ` +
        '(create .venv and install backend/requirements.txt) before starting the app.',
    )
  }

  const proc = spawn(
    pythonBin,
    ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: REPO_ROOT, stdio: 'pipe' },
  )

  const state = { exited: false, exitInfo: null, stderrTail: '' }

  let spawnError = null
  proc.on('error', (err) => {
    spawnError = spawnError || err
    console.error('[backend] process error:', err)
  })

  proc.once('exit', (code, signal) => {
    state.exited = true
    state.exitInfo = { code, signal }
  })

  proc.stdout.on('data', (d) => console.log('[backend]', d.toString().trim()))
  proc.stderr.on('data', (d) => {
    const s = d.toString()
    console.error('[backend]', s.trim())
    state.stderrTail = (state.stderrTail + s).slice(-4000)
  })

  try {
    await waitForHealth(port, HEALTH_TIMEOUT_MS, state)
  } catch (err) {
    if (!state.exited) proc.kill('SIGTERM')
    throw spawnError || err
  }

  const stop = () => {
    if (state.exited) return Promise.resolve()
    return new Promise((resolve) => {
      const forceKillTimer = setTimeout(() => proc.kill('SIGKILL'), 3000)
      proc.once('exit', () => {
        clearTimeout(forceKillTimer)
        resolve()
      })
      proc.kill('SIGTERM')
    })
  }

  return { port, stop }
}

module.exports = { startBackend, findFreePort, waitForHealth }
