# Electron-Python Subprocess Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Electron owns the backend's full lifecycle — spawns it on a dynamically-chosen free port at startup, passes that port to the renderer over a secure IPC channel, and shuts it down cleanly on quit. Replaces today's manual workflow (developer runs `uvicorn` by hand; frontend hardcodes `:8000`).

**Architecture:** `desktop/backend-process.js` finds a free port, spawns the backend via the existing `.venv`'s Python + uvicorn, and health-polls before resolving. `main.js` awaits it before creating the window and exposes the port via `ipcMain.handle`. `preload.js` bridges that to `window.api.getBackendPort()`. `frontend/src/lib/api.ts` calls that when present, falling back to `127.0.0.1:8000` when not (so tests and standalone `vite dev` keep working unchanged). Packaging (a frozen executable) is explicitly out of scope — the backend is still launched via `.venv`, just by Electron instead of a human; the IPC contract doesn't change when packaging lands later.

**Tech Stack:** Node's `child_process`/`net` (no new dependencies), Electron's `ipcMain`/`ipcRenderer`/`contextBridge`.

**Spec:** `docs/superpowers/specs/2026-09-07-web-ui-redesign-design.md` (§5: backend startup/port-passing flow)

## Global Constraints

- `contextIsolation: true`, `nodeIntegration: false` stay unchanged in `desktop/main.js` — the only new renderer-exposed surface is the single `getBackendPort()` call via `contextBridge`, nothing broader.
- Packaging/frozen-executable spawning is out of scope. The backend launches via `<repo-root>/.venv/bin/python -m uvicorn backend.app:app`.
- A hard `SIGKILL` to the Electron process is an accepted, out-of-scope gap (uncatchable by any process, by design) — this plan only guarantees clean shutdown on the normal quit paths (`app.quit()`, window-all-closed, Cmd+Q, SIGTERM), which is what real usage and the smoke tests exercise. Verified: SIGTERM to Electron's main process does fire `before-quit`.
- Commits: short subject + at most one short body line. Task reviewer reports: concise, no padding — test coverage and code rigor stay full.

---

### Task 1: `desktop/backend-process.js` — spawn, health-poll, stop

**Files:**
- Create: `desktop/backend-process.js`

**Interfaces:**
- Produces: `async function startBackend(): Promise<{ port: number, stop: () => Promise<void> }>` (CommonJS export `{ startBackend }`). Throws if the backend doesn't respond healthy within 8s.

- [ ] **Step 1: Write `desktop/backend-process.js`**

Grounded via a real scratch run (port found, uvicorn spawned via `.venv/bin/python`, health-polled, `SIGTERM` shutdown confirmed clean — process fully gone after):

```javascript
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

  proc.stdout.on('data', (d) => console.log('[backend]', d.toString().trim()))
  proc.stderr.on('data', (d) => console.log('[backend]', d.toString().trim()))

  await waitForHealth(port, HEALTH_TIMEOUT_MS)

  const stop = () =>
    new Promise((resolve) => {
      proc.once('exit', () => resolve())
      proc.kill('SIGTERM')
    })

  return { port, stop }
}

module.exports = { startBackend }
```

- [ ] **Step 2: Smoke-test it directly (not via Electron yet)**

```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools/desktop
node -e "
const { startBackend } = require('./backend-process');
(async () => {
  const { port, stop } = await startBackend();
  console.log('PORT:', port);
  const res = await fetch('http://127.0.0.1:' + port + '/health');
  console.log('HEALTH:', await res.text());
  await stop();
  console.log('STOPPED');
})();
"
sleep 1
pgrep -f "uvicorn backend.app:app" && echo "LEAK: process still running" || echo "CLEAN: no orphan process"
```
Expected: `PORT:` a number other than 8000; `HEALTH: {"status":"ok"}`; `STOPPED`; `CLEAN: no orphan process`.

- [ ] **Step 3: Commit**

```bash
git add desktop/backend-process.js
git commit -m "Add backend subprocess manager (spawn, health-poll, stop)"
```

---

### Task 2: Wire it into `main.js`, expose the port via `preload.js`

**Files:**
- Modify: `desktop/main.js`
- Modify: `desktop/preload.js`

**Interfaces:**
- Consumes: `startBackend()` from Task 1.
- Produces: IPC channel `get-backend-port` (main: `ipcMain.handle`); renderer-visible `window.api.getBackendPort(): Promise<number>` (via `contextBridge`).

- [ ] **Step 1: Modify `desktop/main.js`**

Three targeted changes to the existing file (window-open-handler, will-navigate guard, window-all-closed, and activate blocks are unchanged):

1. Line 1 — add `ipcMain` to the destructured import: `const { app, BrowserWindow, ipcMain } = require('electron')`
2. After the `path` require, add: `const { startBackend } = require('./backend-process')`
3. Replace `app.whenReady().then(createWindow)` with:

```javascript
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
```

- [ ] **Step 2: Replace `desktop/preload.js`**

```javascript
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('api', {
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),
})
```

- [ ] **Step 3: Smoke-test the wiring**

```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools/desktop
npm start > /tmp/electron-backend-smoke.log 2>&1 &
ELECTRON_PID=$!
sleep 5
if pgrep -f "uvicorn backend.app:app" > /dev/null; then
  echo "SMOKE TEST PASSED: backend subprocess is running"
else
  echo "SMOKE TEST FAILED"
  cat /tmp/electron-backend-smoke.log
fi
kill $ELECTRON_PID 2>/dev/null
sleep 2
pgrep -f "uvicorn backend.app:app" > /dev/null && echo "LEAK: backend still running" || echo "CLEAN: backend shut down"
pkill -f "electron \." 2>/dev/null
```
Expected: "SMOKE TEST PASSED", then "CLEAN: backend shut down" (killing Electron via its PID sends SIGTERM, which fires `before-quit` — verified separately; this is not a SIGKILL).

- [ ] **Step 4: Commit**

```bash
git add desktop/main.js desktop/preload.js
git commit -m "Wire backend spawning into Electron main process"
```

---

### Task 3: Frontend picks up the dynamic port

**Files:**
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/electron.d.ts`
- Modify: `frontend/src/HealthStatus.svelte`
- Modify: `frontend/src/HealthStatus.test.ts`

**Interfaces:**
- Consumes: `window.api.getBackendPort()` (Task 2), when present.
- Produces: `getBackendUrl(): Promise<string>` — resolves `http://127.0.0.1:<port>` from `window.api` when available, else falls back to `http://127.0.0.1:8000` (this fallback is why `HealthStatus.test.ts`'s 4 existing tests need zero changes — jsdom has no `window.api`).

- [ ] **Step 1: Write `frontend/src/lib/api.ts`**

```typescript
export async function getBackendUrl(): Promise<string> {
  if (typeof window !== 'undefined' && window.api?.getBackendPort) {
    const port = await window.api.getBackendPort()
    return `http://127.0.0.1:${port}`
  }
  return 'http://127.0.0.1:8000'
}
```

- [ ] **Step 2: Write `frontend/src/electron.d.ts`** (ambient type so `window.api` typechecks)

```typescript
export {}

declare global {
  interface Window {
    api?: {
      getBackendPort: () => Promise<number>
    }
  }
}
```

- [ ] **Step 3: Update `HealthStatus.svelte`**

Remove the module-scope `const BACKEND_URL = 'http://127.0.0.1:8000'` line. Add `import { getBackendUrl } from './lib/api'` alongside the existing `onMount` import. Inside `onMount`'s `try` block, before the `fetch` call, add `const backendUrl = await getBackendUrl()`, and change the fetch to `fetch(\`${backendUrl}/health\`)`.

- [ ] **Step 4: Add the new test to `HealthStatus.test.ts`**

Append inside the existing `describe('HealthStatus', ...)` block (after the last existing test) — grounded, already run and passing against the real component:

```typescript
  it('uses the port from window.api when running inside Electron', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) }),
    )
    ;(window as any).api = { getBackendPort: vi.fn().mockResolvedValue(9999) }

    render(HealthStatus)

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith('http://127.0.0.1:9999/health')
    })

    delete (window as any).api
  })
```
(Setting `window.api` directly, not replacing the whole `window` global — spreading `window` into a stub loses jsdom's built-in behavior.)

- [ ] **Step 5: Run the full suite**

```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools/frontend
npm test
npm run check
npm run build
```
Expected: 5 passed (4 existing unchanged + 1 new), 0 typecheck errors, build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/electron.d.ts frontend/src/HealthStatus.svelte frontend/src/HealthStatus.test.ts
git commit -m "Frontend fetches backend port from Electron instead of hardcoding it"
```

---

### Task 4: Update dev docs, full end-to-end proof

**Files:**
- Modify: `BACKEND_OVERVIEW.md`

**Interfaces:**
- Consumes: Tasks 1-3, all of them together — this is the milestone's acceptance check.

- [ ] **Step 1: Update the "Frontend/Electron dev setup" section**

Remove the "start the backend manually" instruction (now obsolete — Electron does it). Keep the three-separate-`npm install` note (still true; npm workspaces is still a future follow-up). New text: install in the three places, then just `npm run dev` — Electron spawns the backend itself on a dynamically-chosen free port, passed to the renderer over IPC.

- [ ] **Step 2: Full end-to-end proof**

```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools
npm run dev > /tmp/e2e-dev-smoke.log 2>&1 &
DEV_PID=$!
sleep 6
PORT=$(grep -o "Uvicorn running on http://127.0.0.1:[0-9]*" /tmp/e2e-dev-smoke.log | grep -o "[0-9]*$" | head -1)
echo "Detected backend port: $PORT"
if [ "$PORT" != "8000" ] && [ -n "$PORT" ]; then
  curl -s http://127.0.0.1:$PORT/health && echo
  echo "SMOKE TEST PASSED: backend auto-spawned on dynamic port $PORT"
else
  echo "SMOKE TEST FAILED: expected a non-8000 dynamic port, got: $PORT"
  cat /tmp/e2e-dev-smoke.log
fi
kill $DEV_PID 2>/dev/null
pkill -f "electron \." 2>/dev/null
sleep 2
pgrep -f "uvicorn backend.app:app" > /dev/null && echo "LEAK: backend still running" || echo "CLEAN: backend shut down after quit"
```
Expected: a detected port that is NOT 8000, a healthy `{"status":"ok"}` response from it, and "CLEAN: backend shut down after quit". (Task 3's own test already proves the renderer correctly uses `window.api`'s port when present — this step proves the real, whole pipeline does the same, end to end, not just the unit-level contract.)

- [ ] **Step 3: Commit**

```bash
git add BACKEND_OVERVIEW.md
git commit -m "Update dev docs: Electron now spawns the backend automatically"
```
