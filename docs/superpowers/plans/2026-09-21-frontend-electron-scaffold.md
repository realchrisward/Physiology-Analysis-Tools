# Frontend & Electron Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Electron + Svelte walking skeleton — a real desktop window rendering a real Svelte component that calls the real backend from Milestone 1 — proving the three pieces (Electron, Svelte/Vite, FastAPI backend) actually talk to each other before any real UI screens are built.

**Architecture:** `frontend/` is a Vite + Svelte 5 + TypeScript SPA (no SvelteKit — a single-page local control panel doesn't need SSR/routing). `desktop/` is the Electron main process that loads the Vite dev server in dev mode or the built `frontend/dist/` in packaged mode. A root-level `package.json` orchestrates running both together for local dev. No Electron↔Python subprocess wiring yet (backend is started manually for now) and no packaging/electron-builder — both are later milestones.

**Tech Stack:** Node v22.18.0 / npm 11.7.0 (confirmed installed via nvm), Vite 8, Svelte 5 (runes), TypeScript ~6.0, vitest 5 + @testing-library/svelte for component tests, Electron (latest via `npm install --save-dev electron`), concurrently + wait-on for dev orchestration.

**Spec:** `docs/superpowers/specs/2026-09-07-web-ui-redesign-design.md` (architecture: Electron shell, Svelte frontend, repo shape naming `frontend/`/`desktop/`)

## Global Constraints

- Package manager is **npm** everywhere in this plan (not pnpm/yarn), even though both are also present on this machine — consistency across `frontend/`, `desktop/`, and the repo root.
- The scaffolded template is **Svelte 5** — components use runes syntax (`$state`, `$props()`, `$effect`) and the `mount()` API, never Svelte 4's `export let` / `$:` reactive statements.
- Backend URL for this milestone is **hardcoded** to `http://127.0.0.1:8000` in the frontend — real port-passing from a spawned Python subprocess is a later milestone's job, not this one.
- Electron's `webPreferences` must have **`contextIsolation: true`, `nodeIntegration: false`** — non-negotiable security baseline, even though no privileged APIs are exposed yet.
- **electron-builder / packaging is out of scope** for this plan entirely.
- Commits: short subject line + at most one short body line (this round's convention — no multi-sentence bodies).
- Task reviewer reports: concise, no padding — but do not reduce test coverage or code rigor to get there.

---

### Task 1: Scaffold `frontend/` (Vite + Svelte 5 + TypeScript + vitest)

**Files:**
- Create: `frontend/` (via `npm create vite@latest frontend -- --template svelte-ts` — generates `package.json`, `vite.config.ts`, `tsconfig*.json`, `src/App.svelte`, `src/main.ts`, `src/lib/Counter.svelte`, etc.)
- Modify: `frontend/vite.config.ts`
- Create: `frontend/src/test-setup.ts`
- Test: `frontend/src/lib/Counter.test.ts`

**Interfaces:**
- Produces: a working `frontend/` project with `npm run dev` (Vite dev server, default port 5173), `npm run build` (outputs `frontend/dist/`), `npm run check` (svelte-check + tsc, already in the generated template), `npm test` (vitest run). Later tasks add components under `frontend/src/`.

- [ ] **Step 1: Scaffold the project**

```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools
npm create vite@latest frontend -- --template svelte-ts
```
Expected: creates `frontend/` non-interactively (the `--template` flag skips the prompt), prints "Done. Now run: cd frontend / npm install / npm run dev".

- [ ] **Step 2: Install and verify the baseline**

```bash
cd frontend
npm install
npm run build
npm run check
```
Expected: `npm run build` succeeds (`✓ built in <time>`); `npm run check` reports `0 ERRORS 0 WARNINGS`. This confirms the untouched scaffold is sound before we change anything.

- [ ] **Step 3: Install test tooling**

```bash
npm install -D vitest @testing-library/svelte jsdom @testing-library/jest-dom
```

- [ ] **Step 4: Add the `test` script**

In `frontend/package.json`, add to `"scripts"`:
```json
"test": "vitest run"
```

- [ ] **Step 5: Write the failing test**

`frontend/src/lib/Counter.test.ts` (exercises the template's own generated `Counter.svelte` — proves the test harness works against real Svelte 5 component behavior, not a mock):
```typescript
import { render, screen, fireEvent } from '@testing-library/svelte'
import { describe, expect, it } from 'vitest'
import Counter from './Counter.svelte'

describe('Counter', () => {
  it('increments when clicked', async () => {
    render(Counter)
    const button = screen.getByRole('button')
    expect(button).toHaveTextContent('Count is 0')

    await fireEvent.click(button)

    expect(button).toHaveTextContent('Count is 1')
  })
})
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — `Svelte error: lifecycle_function_unavailable`, `mount(...) is not available on the server`. This happens because Vite/Vitest resolves Svelte's server-side (SSR) build by default; nothing yet tells it to resolve the browser build for tests.

- [ ] **Step 7: Fix the config**

Replace `frontend/vite.config.ts` entirely:
```typescript
/// <reference types="vitest/config" />
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [svelte()],
  resolve: {
    conditions: ['browser'],
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
  },
})
```
The triple-slash reference at the top is required — without it, `npm run check`'s `tsc -p tsconfig.node.json` step fails with "Object literal may only specify known properties, and 'test' does not exist in type 'UserConfigExport'" (Vitest's `test` field isn't part of plain Vite's config type).

- [ ] **Step 8: Add the test setup file**

`frontend/src/test-setup.ts`:
```typescript
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/svelte'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
```
`cleanup()` unmounts rendered components between tests — without it, a later task's second test in the same file will find duplicate DOM elements from a prior test's un-cleaned render.

- [ ] **Step 9: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — "Test Files 1 passed (1)", "Tests 1 passed (1)".

- [ ] **Step 10: Re-verify build and check still pass**

```bash
npm run build
npm run check
```
Expected: both still succeed — confirms the `vite.config.ts` changes didn't break typecheck or the production build.

- [ ] **Step 11: Commit**

```bash
git add frontend/
git commit -m "Scaffold Vite + Svelte 5 + TS frontend with vitest"
```

---

### Task 2: `HealthStatus.svelte` — the vertical-slice proof

**Files:**
- Create: `frontend/src/HealthStatus.svelte`
- Create: `frontend/src/HealthStatus.test.ts`
- Modify: `frontend/src/App.svelte`
- Delete: `frontend/src/lib/Counter.svelte`, `frontend/src/lib/Counter.test.ts`

**Interfaces:**
- Consumes: the backend's `GET /health` (Milestone 1, `backend/app.py`) — returns `{"status": "ok"}`.
- Produces: `<HealthStatus />` — a self-contained Svelte component with no props, rendered by `App.svelte`.

- [ ] **Step 1: Write the failing test**

`frontend/src/HealthStatus.test.ts`:
```typescript
import { render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HealthStatus from './HealthStatus.svelte'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HealthStatus', () => {
  it('shows ok when the backend responds healthy', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: 'ok' }),
      }),
    )

    render(HealthStatus)

    await waitFor(() => {
      expect(screen.getByTestId('health-status')).toHaveTextContent('Backend: ok')
    })
  })

  it('shows error when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network error')))

    render(HealthStatus)

    await waitFor(() => {
      expect(screen.getByTestId('health-status')).toHaveTextContent('Backend: error')
    })
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — `Cannot find module './HealthStatus.svelte'` (component doesn't exist yet).

- [ ] **Step 3: Write the component**

`frontend/src/HealthStatus.svelte`:
```svelte
<script lang="ts">
  import { onMount } from 'svelte'

  const BACKEND_URL = 'http://127.0.0.1:8000'

  type Status = 'loading' | 'ok' | 'error'

  let status: Status = $state('loading')
  let errorMessage: string = $state('')

  onMount(async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/health`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
      status = data.status === 'ok' ? 'ok' : 'error'
    } catch (e) {
      status = 'error'
      errorMessage = e instanceof Error ? e.message : String(e)
    }
  })
</script>

{#if status === 'loading'}
  <p data-testid="health-status">Checking backend...</p>
{:else if status === 'ok'}
  <p data-testid="health-status">Backend: ok</p>
{:else}
  <p data-testid="health-status">Backend: error{errorMessage ? ` (${errorMessage})` : ''}</p>
{/if}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — "Tests 2 passed" for `HealthStatus.test.ts` (3 passed total including Task 1's `Counter.test.ts`, until Step 6 removes it).

- [ ] **Step 5: Wire it into `App.svelte`**

Replace `frontend/src/App.svelte` entirely:
```svelte
<script lang="ts">
  import HealthStatus from './HealthStatus.svelte'
</script>

<main>
  <h1>Physiology Analysis Tools</h1>
  <HealthStatus />
</main>
```

- [ ] **Step 6: Remove the superseded demo component**

```bash
rm frontend/src/lib/Counter.svelte frontend/src/lib/Counter.test.ts
```

- [ ] **Step 7: Run the full test suite, check, and build**

```bash
npm test
npm run check
npm run build
```
Expected: `npm test` → "Tests 2 passed" (only `HealthStatus.test.ts` remains); `npm run check` → 0 errors (confirms no dangling references to the deleted `Counter.svelte` or the template's unused hero/logo assets, since `App.svelte` no longer imports them); `npm run build` succeeds.

- [ ] **Step 8: Commit**

```bash
git add frontend/
git commit -m "Add HealthStatus component, replace demo App content"
```

---

### Task 3: Scaffold `desktop/` (Electron main process)

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/main.js`
- Create: `desktop/preload.js`

**Interfaces:**
- Produces: `desktop/main.js` — launched via `cd desktop && npm start`. Opens a `BrowserWindow` loading `http://localhost:5173` when not packaged, or `frontend/dist/index.html` when `app.isPackaged`. `webPreferences: { contextIsolation: true, nodeIntegration: false, preload: <path> }`.

- [ ] **Step 1: Create `desktop/package.json`**

```json
{
  "name": "desktop",
  "private": true,
  "version": "0.0.0",
  "main": "main.js",
  "scripts": {
    "start": "electron ."
  }
}
```

- [ ] **Step 2: Install Electron**

```bash
cd desktop
npm install --save-dev electron
```
Expected: adds `electron` to `desktop/package.json`'s `devDependencies`; `npx electron --version` prints a version.

- [ ] **Step 3: Write the preload script**

`desktop/preload.js`:
```javascript
// Intentionally empty for now. No privileged APIs are exposed to the
// renderer yet - contextIsolation + nodeIntegration:false (set in main.js)
// are the security baseline real IPC (spawning/talking to the Python
// backend) will build on in a later milestone.
```

- [ ] **Step 4: Write the main process**

`desktop/main.js`:
```javascript
const { app, BrowserWindow } = require('electron')
const path = require('node:path')

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

  if (app.isPackaged) {
    win.loadFile(path.join(__dirname, '..', 'frontend', 'dist', 'index.html'))
  } else {
    win.loadURL(DEV_SERVER_URL)
  }
}

app.whenReady().then(createWindow)

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
```

- [ ] **Step 5: Smoke-test the process launches without crashing**

Nothing needs to be listening on :5173 for this check — the window will just show a load-failure page, which is fine here; this step proves the Electron *process* itself is sound (correct `webPreferences`, no syntax errors, doesn't crash on startup). Loading real content end-to-end is Task 4.

```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools/desktop
npm start > /tmp/electron-smoke.log 2>&1 &
ELECTRON_PID=$!
sleep 3
if kill -0 $ELECTRON_PID 2>/dev/null; then
  echo "SMOKE TEST PASSED: Electron process is alive after 3s"
else
  echo "SMOKE TEST FAILED: Electron process exited early"
  cat /tmp/electron-smoke.log
fi
kill $ELECTRON_PID 2>/dev/null
pkill -f "electron \." 2>/dev/null  # npm spawns electron as a child - kill it too, don't leave it orphaned
```
Expected: "SMOKE TEST PASSED: Electron process is alive after 3s"; `/tmp/electron-smoke.log` contains no uncaught-exception stack traces.

- [ ] **Step 6: Commit**

```bash
git add desktop/
git commit -m "Scaffold Electron main process"
```

---

### Task 4: Dev orchestration + full-pipeline smoke test

**Files:**
- Create: `package.json` (repo root)

**Interfaces:**
- Consumes: `frontend`'s `npm run dev` (Task 1), `desktop`'s `npm start` (Task 3).
- Produces: root-level `npm run dev` — the single command a developer runs to start the Vite dev server and, once it's reachable, launch Electron pointed at it.

- [ ] **Step 1: Create the root `package.json`**

```json
{
  "name": "physiology-analysis-tools",
  "private": true,
  "version": "0.0.0",
  "scripts": {
    "dev": "concurrently -k -n vite,electron \"npm run dev --prefix frontend\" \"wait-on http://localhost:5173 && npm start --prefix desktop\""
  }
}
```

- [ ] **Step 2: Install orchestration tooling**

```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools
npm install -D concurrently wait-on
```

- [ ] **Step 3: Full-pipeline smoke test**

```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools
npm run dev > /tmp/dev-smoke.log 2>&1 &
DEV_PID=$!
sleep 8
if pgrep -f "electron \." > /dev/null; then
  echo "SMOKE TEST PASSED: Electron launched via the full dev pipeline"
else
  echo "SMOKE TEST FAILED: Electron did not launch"
  cat /tmp/dev-smoke.log
fi
kill $DEV_PID 2>/dev/null
pkill -f "electron \." 2>/dev/null
pkill -f "vite" 2>/dev/null
```
Expected: "SMOKE TEST PASSED: Electron launched via the full dev pipeline"; `/tmp/dev-smoke.log` shows Vite's dev server ready message and no Electron crash output. (The window will show `HealthStatus`'s "Backend: error" state since the Python backend isn't running for this test — that's expected and fine; `HealthStatus`'s own two branches are already covered by Task 2's mocked-fetch tests. This step only proves the three processes are wired together correctly.)

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json
git commit -m "Add root dev script orchestrating Vite + Electron"
```
