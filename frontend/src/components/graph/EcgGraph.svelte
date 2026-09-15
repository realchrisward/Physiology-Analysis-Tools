<script lang="ts">
  import { onMount } from 'svelte'
  import uPlot from 'uplot'
  import 'uplot/dist/uPlot.min.css'
  import { getChannelWindow } from '../../lib/api/windowing'

  let { path, channel }: { path: string; channel: string } = $props()

  const DEBOUNCE_MS = 150
  const HEIGHT = 320
  const ZOOM_FACTOR = 0.75

  let containerEl: HTMLDivElement | undefined = $state()
  // Tracked via `bind:clientWidth` below. Read directly off `containerEl`
  // for the one-off initial-mount fetch instead (see onMount) so that fetch
  // isn't racing this binding's own first effect run.
  let containerWidth: number = $state(0)
  let error: string | null = $state(null)

  // Plain (non-reactive) instance state: the live uPlot instance, the
  // full-extent range for "Reset view", and the pan/zoom debounce timer.
  // None of these need to drive a re-render themselves — they're read from
  // event handlers and effects, not the template.
  let chart: uPlot | undefined
  let fullExtent: { start: number; end: number } | undefined
  let debounceTimer: ReturnType<typeof setTimeout> | undefined
  // Set on the first `containerWidth` effect run (the binding's own initial
  // read), so later effect runs can tell an actual resize apart from that.
  let previousWidth: number | undefined

  function resolutionFor(width: number): number {
    return Math.max(1, Math.round(width))
  }

  async function loadWindow(start: number, end: number, width: number): Promise<{ x: number[]; y: number[] } | null> {
    const result = await getChannelWindow(path, channel, start, end, resolutionFor(width))
    if (result.status !== 'ok' || result.error) {
      error = result.error ?? 'Failed to load channel window'
      return null
    }
    error = null
    return { x: result.x, y: result.y }
  }

  async function refetch(min: number, max: number) {
    const data = await loadWindow(min, max, containerWidth)
    if (data && chart) {
      chart.setData([data.x, data.y], false)
    }
  }

  function scheduleRefetch(min: number, max: number) {
    if (debounceTimer !== undefined) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      debounceTimer = undefined
      void refetch(min, max)
    }, DEBOUNCE_MS)
  }

  // Left-drag (no modifier) pans: mutates the x-scale directly on every
  // mousemove for immediate visual feedback, then schedules one debounced
  // re-fetch once the drag ends.
  function handleDragStart(u: uPlot, downEvent: MouseEvent) {
    if (downEvent.button !== 0) return
    downEvent.preventDefault()

    const startX = downEvent.clientX
    const scaleMin0 = u.scales.x.min ?? 0
    const scaleMax0 = u.scales.x.max ?? 0
    const unitsPerPx = u.posToVal(1, 'x') - u.posToVal(0, 'x')

    function onMove(moveEvent: MouseEvent) {
      const dx = unitsPerPx * (moveEvent.clientX - startX)
      u.setScale('x', { min: scaleMin0 - dx, max: scaleMax0 - dx })
    }

    function onUp() {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      const { min, max } = u.scales.x
      if (min != null && max != null) scheduleRefetch(min, max)
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  // Wheel zooms in/out centered on the cursor's x position, then schedules a
  // debounced re-fetch of the new visible range.
  function handleWheel(u: uPlot, wheelEvent: WheelEvent) {
    wheelEvent.preventDefault()

    const rect = u.over.getBoundingClientRect()
    const left = wheelEvent.clientX - rect.left
    // jsdom never lays elements out (getBoundingClientRect is always
    // zeroed), so rect.width is 0 in tests regardless of the mocked
    // clientWidth used to size the chart itself — fall back to centering
    // rather than dividing by zero.
    const leftPct = rect.width === 0 ? 0.5 : left / rect.width
    const xVal = u.posToVal(left, 'x')
    const oldMin = u.scales.x.min ?? 0
    const oldMax = u.scales.x.max ?? 0
    const oldRange = oldMax - oldMin

    const newRange = wheelEvent.deltaY < 0 ? oldRange * ZOOM_FACTOR : oldRange / ZOOM_FACTOR
    const newMin = xVal - leftPct * newRange
    const newMax = newMin + newRange

    u.setScale('x', { min: newMin, max: newMax })
    scheduleRefetch(newMin, newMax)
  }

  function panZoomPlugin(): uPlot.Plugin {
    return {
      hooks: {
        ready: (u) => {
          u.over.addEventListener('mousedown', (e) => handleDragStart(u, e))
          u.over.addEventListener('wheel', (e) => handleWheel(u, e))
        },
      },
    }
  }

  onMount(() => {
    let cancelled = false

    void (async () => {
      const width = containerEl?.clientWidth ?? 0
      const data = await loadWindow(0, Number.MAX_SAFE_INTEGER, width)
      if (cancelled || !data || !containerEl) return

      fullExtent = { start: data.x[0] ?? 0, end: data.x[data.x.length - 1] ?? 0 }

      chart = new uPlot(
        {
          width,
          height: HEIGHT,
          scales: { x: { time: false } },
          series: [{}, { stroke: '#1a7f37' }],
          plugins: [panZoomPlugin()],
        },
        [data.x, data.y],
        containerEl,
      )
    })()

    return () => {
      cancelled = true
      if (debounceTimer !== undefined) clearTimeout(debounceTimer)
      chart?.destroy()
      chart = undefined
    }
  })

  // Reacts to `bind:clientWidth` changes on the container (window
  // resizes/layout changes). The binding's own effect fires once
  // synchronously on mount with the starting width — recorded via
  // `previousWidth` but not treated as a resize, since the initial chart
  // construction above already used that same width.
  $effect(() => {
    const width = containerWidth
    if (previousWidth === undefined) {
      previousWidth = width
      return
    }
    if (width === previousWidth) return
    previousWidth = width

    if (!chart) return
    chart.setSize({ width, height: HEIGHT })
    const { min, max } = chart.scales.x
    if (min != null && max != null) scheduleRefetch(min, max)
  })

  function handleResetView() {
    if (!chart || !fullExtent) return
    if (debounceTimer !== undefined) {
      clearTimeout(debounceTimer)
      debounceTimer = undefined
    }
    chart.setScale('x', { min: fullExtent.start, max: fullExtent.end })
    void refetch(fullExtent.start, fullExtent.end)
  }
</script>

<div data-testid="ecg-graph">
  <button data-testid="reset-view-button" onclick={handleResetView}>Reset view</button>

  {#if error}
    <div data-testid="ecg-graph-error">{error}</div>
  {/if}

  <div data-testid="ecg-graph-container" bind:this={containerEl} bind:clientWidth={containerWidth}></div>
</div>
