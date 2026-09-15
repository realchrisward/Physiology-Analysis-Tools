import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/svelte'
import { afterEach } from 'vitest'

// jsdom has no `window.matchMedia` at all (unlike canvas's `getContext`,
// which at least exists but throws — see below). uPlot calls `matchMedia` at
// *module import time* (to track devicePixelRatio changes), not lazily when
// a chart is constructed, so this stub has to be installed here, in the
// global setup file, before any test file's own top-level `import uplot`
// (transitively, via EcgGraph.svelte) ever runs. A local stub inside a test
// file is too late: ES module imports are hoisted and evaluate before any of
// that file's own top-level statements, so by the time a test file could
// call `vi.stubGlobal`, uPlot's module body — and its `matchMedia` call —
// has already executed.
window.matchMedia ??= () =>
  ({
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  }) as unknown as MediaQueryList

// jsdom also has no `ResizeObserver`. Svelte's `bind:clientWidth` (used by
// EcgGraph to track its container's width) constructs one lazily, the first
// time such a binding is observed, so — unlike matchMedia — a local stub
// inside a test's `beforeEach` would technically be early enough. It lives
// here anyway since every test that renders EcgGraph needs it. Real
// resizing is never simulated (jsdom doesn't lay elements out), so
// `observe`/`unobserve`/`disconnect` are no-ops: `bind:clientWidth`'s own
// effect reads `element.clientWidth` once synchronously on mount regardless,
// which is enough for tests that pre-set `clientWidth` before rendering.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

// jsdom implements neither `Path2D` nor a working 2D canvas context (calling
// `HTMLCanvasElement.prototype.getContext('2d')` throws "Not implemented" —
// jsdom deliberately doesn't ship a rasterizer). uPlot (see
// components/graph/EcgGraph.svelte, first introduced for F3 Task 2) draws
// through both on every render, so any test that actually constructs a live
// uPlot instance needs stand-ins for both. These are deliberately minimal:
// no-op method bodies that satisfy uPlot's call shape without asserting
// anything about drawing — the tests that exercise EcgGraph assert on
// fetch calls and DOM/state, never on pixels.
class StubPath2D {
  constructor(_path?: unknown) {}
  addPath() {}
  closePath() {}
  moveTo() {}
  lineTo() {}
  bezierCurveTo() {}
  quadraticCurveTo() {}
  arc() {}
  arcTo() {}
  ellipse() {}
  rect() {}
  roundRect() {}
}
;(globalThis as unknown as { Path2D: unknown }).Path2D = StubPath2D

function stubCanvasContext() {
  const noop = () => {}
  return {
    save: noop,
    restore: noop,
    translate: noop,
    rotate: noop,
    beginPath: noop,
    moveTo: noop,
    lineTo: noop,
    rect: noop,
    clip: noop,
    fill: noop,
    stroke: noop,
    clearRect: noop,
    setLineDash: noop,
    fillText: noop,
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 0,
    lineCap: '',
    lineJoin: '',
    font: '',
    textAlign: '',
    textBaseline: '',
    globalAlpha: 1,
  } as unknown as CanvasRenderingContext2D
}

// jsdom's own `getContext` exists but throws "Not implemented" when called
// with '2d' (it has no rasterizer), so this must unconditionally replace it
// rather than fill in a gap.
HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, contextId: string) {
  return contextId === '2d' ? stubCanvasContext() : null
} as typeof HTMLCanvasElement.prototype.getContext

afterEach(() => {
  cleanup()
})
