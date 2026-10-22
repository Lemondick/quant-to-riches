import test from 'node:test'
import assert from 'node:assert/strict'
import { createDataApi, clearEtagCache } from '../src/services/dataApi.ts'

function mockResponse({ status = 200, body = {}, headers = {} }) {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]))
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => lower[name.toLowerCase()] ?? null },
    json: async () => structuredClone(body),
  }
}

function makeFetcher(handler) {
  const calls = []
  const fetcher = async (url, init = {}) => {
    calls.push({ url, headers: init.headers ?? {} })
    return handler(calls.length, calls[calls.length - 1])
  }
  return { fetcher, calls }
}

test('ETag: second GET sends If-None-Match and 304 returns cached data', async () => {
  clearEtagCache()
  const { fetcher, calls } = makeFetcher((n) =>
    n === 1 ? mockResponse({ body: { v: 1 }, headers: { ETag: '"abc123"' } }) : mockResponse({ status: 304 }),
  )
  const api = createDataApi('http://localhost:5000', fetcher)
  const first = await api.read('/market/snapshot')
  assert.deepEqual(first, { v: 1 })
  const second = await api.read('/market/snapshot')
  assert.deepEqual(second, { v: 1 })
  assert.equal(calls.length, 2)
  assert.equal(calls[1].headers['If-None-Match'], '"abc123"')
})

test('ETag: changed body returns 200 with new ETag', async () => {
  clearEtagCache()
  const { fetcher, calls } = makeFetcher((n) =>
    n === 1
      ? mockResponse({ body: { v: 1 }, headers: { ETag: '"e1"' } })
      : mockResponse({ body: { v: 2 }, headers: { ETag: '"e2"' } }),
  )
  const api = createDataApi('http://localhost:5000', fetcher)
  assert.deepEqual(await api.read('/valuation'), { v: 1 })
  assert.deepEqual(await api.read('/valuation'), { v: 2 })
  assert.equal(calls[1].headers['If-None-Match'], '"e1"')
  // third read must use the refreshed etag
  const { fetcher: f2, calls: c2 } = makeFetcher(() => mockResponse({ status: 304 }))
  const api2 = createDataApi('http://localhost:5000', f2)
  assert.deepEqual(await api2.read('/valuation'), { v: 2 })
  assert.equal(c2[0].headers['If-None-Match'], '"e2"')
})

test('ETag: POST never sends If-None-Match and never caches', async () => {
  clearEtagCache()
  const { fetcher, calls } = makeFetcher(() => mockResponse({ body: { ok: true } }))
  const api = createDataApi('http://localhost:5000', fetcher)
  await api.write('/ledger', { x: 1 }, 'csrf', 'scope')
  assert.ok(!('If-None-Match' in calls[0].headers))
})

test('ETag: clearEtagCache drops conditional headers', async () => {
  clearEtagCache()
  const { fetcher, calls } = makeFetcher((n) =>
    n === 1 ? mockResponse({ body: { v: 1 }, headers: { ETag: '"e1"' } }) : mockResponse({ body: { v: 1 } }),
  )
  const api = createDataApi('http://localhost:5000', fetcher)
  await api.read('/market/news')
  clearEtagCache()
  await api.read('/market/news')
  assert.ok(!('If-None-Match' in calls[1].headers))
})
