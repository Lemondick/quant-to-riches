import test from 'node:test'
import assert from 'node:assert/strict'
import { createDataApi } from '../src/services/dataApi.ts'

function pendingApi(t) {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let complete, requestSignal
  const api = createDataApi('https://synthetic.invalid', (_url, { signal }) => {
    requestSignal = signal
    return new Promise((resolve, reject) => {
      complete = (body) => resolve(Response.json(body))
      const abort = () => reject(new DOMException('synthetic request aborted', 'AbortError'))
      if (signal.aborted) abort()
      else signal.addEventListener('abort', abort, { once: true })
    })
  })
  return { api, complete: (body) => complete(body), aborted: () => requestSignal.aborted }
}

test('a portfolio backtest can finish after the ordinary 15 second request deadline', async (t) => {
  const pending = pendingApi(t)
  const result = pending.api.write('/backtest/portfolio', {}, 'csrf', 'scope')
  t.mock.timers.tick(20000)
  pending.complete({ metrics: { total_return: 0.1 } })
  assert.deepEqual(await result, { metrics: { total_return: 0.1 } })
})

for (const { path, method, deadline } of [
  { path: '/backtest/portfolio', method: 'POST', deadline: 605000 },
  { path: '/dca/smart/synthetic-plan/backtest', method: 'POST', deadline: 305000 },
  { path: '/research/strategy-settings/preview', method: 'POST', deadline: 65000 },
  { path: '/market/snapshot', method: 'GET', deadline: 15000 },
  { path: '/backtest/portfolio', method: 'GET', deadline: 15000 },
  { path: '/dca/smart/synthetic-plan/backtest-export', method: 'POST', deadline: 15000 },
]) {
  test(`${method} ${path} times out at ${deadline}ms and not earlier`, async (t) => {
    const pending = pendingApi(t)
    const result = method === 'GET' ? pending.api.read(path) : pending.api.write(path, {}, 'csrf', 'scope')
    const rejected = assert.rejects(result, /读取超时/)
    t.mock.timers.tick(deadline - 1)
    assert.equal(pending.aborted(), false)
    t.mock.timers.tick(1)
    await rejected
    assert.equal(pending.aborted(), true)
  })
}

test('a long backtest still stops immediately when its caller cancels', async (t) => {
  const pending = pendingApi(t)
  const controller = new AbortController()
  const result = pending.api.write('/backtest/portfolio', {}, 'csrf', 'scope', 'POST', controller.signal)
  controller.abort()
  await assert.rejects(result, /读取已取消/)
  assert.equal(pending.aborted(), true)
})
