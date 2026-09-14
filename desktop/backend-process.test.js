const { test } = require('node:test')
const assert = require('node:assert')
const http = require('node:http')
const { startBackend, findFreePort, waitForHealth } = require('./backend-process')

test('findFreePort returns distinct usable ports', async () => {
  const a = await findFreePort()
  const b = await findFreePort()
  assert.notStrictEqual(a, b)
  assert.ok(a > 0 && a < 65536)
})

test('waitForHealth resolves once a real status:ok server comes up', async () => {
  const port = await findFreePort()
  const state = { exited: false, exitInfo: null, stderrTail: '' }

  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ status: 'ok' }))
  })

  await new Promise((resolve) => {
    setTimeout(() => server.listen(port, '127.0.0.1', resolve), 300)
  })

  await waitForHealth(port, 3000, state)

  await new Promise((resolve) => server.close(resolve))
})

test('waitForHealth times out with a descriptive error when nothing listens', async () => {
  const port = await findFreePort()
  const state = { exited: false, exitInfo: null, stderrTail: '' }

  await assert.rejects(
    () => waitForHealth(port, 500, state),
    /did not become healthy/,
  )
})

test('waitForHealth fails fast (not after the full timeout) when the process already exited', async () => {
  const port = await findFreePort()
  const state = {
    exited: true,
    exitInfo: { code: 1, signal: null },
    stderrTail: 'ImportError: no module named backend\n',
  }

  const start = Date.now()
  await assert.rejects(
    () => waitForHealth(port, 5000, state),
    /exited.*before becoming healthy/,
  )
  assert.ok(Date.now() - start < 500, 'should fail fast, not wait for the full timeout')
})

test('startBackend spawns a real, healthy backend and stop() cleans it up', async () => {
  const { port, stop } = await startBackend()
  assert.ok(port > 0)

  const res = await fetch(`http://127.0.0.1:${port}/health`)
  assert.strictEqual(res.status, 200)
  const body = await res.json()
  assert.strictEqual(body.status, 'ok')

  await stop()
})
