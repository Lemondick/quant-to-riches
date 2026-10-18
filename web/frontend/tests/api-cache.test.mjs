import test from 'node:test'
import assert from 'node:assert/strict'
import { createSWRCache } from '../src/services/apiCache.ts'
import { ApiError, apiRoot, createDataApi, validateDashboard } from '../src/services/dataApi.ts'

test('SWR displays retained data while one deduplicated revalidation is pending', async () => {
  let now = 1000,
    calls = 0,
    finish
  const cache = createSWRCache(() => now)
  await cache.revalidate('alice:summary', async () => ({ total: '100.00' }), 100)
  now += 200
  const load = () => {
    calls++
    return new Promise((resolve) => {
      finish = resolve
    })
  }
  const first = cache.revalidate('alice:summary', load, 100)
  const second = cache.revalidate('alice:summary', load, 100)
  await Promise.resolve()
  assert.equal(first, second)
  assert.equal(calls, 1)
  assert.deepEqual(cache.peek('alice:summary', 100).data, { total: '100.00' })
  assert.equal(cache.peek('alice:summary', 100).stale, true)
  finish({ total: '101.00' })
  await first
  assert.deepEqual(cache.peek('alice:summary', 100).data, { total: '101.00' })
})

test('cache clones values, respects identity keys, and rejects results after invalidation', async () => {
  const cache = createSWRCache()
  const original = { items: ['verified'] }
  await cache.revalidate('alice:session-1', async () => original)
  original.items.push('changed')
  cache.peek('alice:session-1').data.items.push('caller-change')
  assert.deepEqual(cache.peek('alice:session-1').data, { items: ['verified'] })
  assert.equal(cache.peek('bob:session-2').data, undefined)
  let finish
  const pending = cache.revalidate(
    'alice:session-1',
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
    0,
  )
  await Promise.resolve()
  cache.invalidate()
  finish({ items: ['late'] })
  await pending
  assert.equal(cache.peek('alice:session-1').data, undefined)
})

test('failed revalidation preserves verified evidence and exposes an error and stale status', async () => {
  let now = 1000
  const cache = createSWRCache(() => now)
  await cache.revalidate('evidence', async () => ({ observed_at: '2026-10-16' }))
  await assert.rejects(
    cache.revalidate(
      'evidence',
      async () => {
        throw new Error('unavailable')
      },
      0,
    ),
  )
  assert.equal(cache.peek('evidence').data.observed_at, '2026-10-16')
  assert.equal(cache.peek('evidence').stale, true)
  now += 300001
  assert.equal(cache.peek('evidence').data, undefined)
})

test('fresh cache suppresses duplicate fetches until its TTL', async () => {
  const cache = createSWRCache(() => 1000)
  let calls = 0
  const read = async () => ++calls
  await cache.revalidate('market', read)
  await cache.revalidate('market', read)
  assert.equal(calls, 1)
  await cache.revalidate('market', read, 15000, true)
  assert.equal(calls, 2)
})

test('account writes mark mounted resources stale without clearing their visible data', async () => {
  const cache = createSWRCache()
  await cache.revalidate('account:sessions', async () => ({ items: ['current'] }))
  await cache.revalidate('account:models', async () => ({ revision: 1 }))
  cache.markStale('account:')
  assert.deepEqual(cache.peek('account:sessions').data, { items: ['current'] })
  assert.equal(cache.peek('account:sessions').stale, true)
  let calls = 0
  await cache.revalidate('account:models', async () => {
    calls++
    return { revision: 2 }
  })
  assert.equal(calls, 1)
  assert.equal(cache.peek('account:models').stale, false)
  cache.invalidate('account:')
  assert.equal(cache.peek('account:sessions').data, undefined)
})

test('API uses trace IDs, rejects redirects and errors instead of returning sample data', async () => {
  let options, url
  const api = createDataApi('/api', async (input, init) => {
    url = input
    options = init
    return new Response('{}', { status: 401 })
  })
  await assert.rejects(api.read('/dashboard/summary'), (error) => error instanceof ApiError && error.status === 401)
  assert.equal(url, '/api/v1/dashboard/summary')
  assert.match(options.headers['X-Trace-Id'], /^[0-9a-f]{32}$/)
  assert.equal(options.redirect, 'error')
  assert.equal(options.credentials, 'same-origin')
  assert.equal(apiRoot('/api/v1/'), '/api/v1')
  assert.equal(apiRoot(''), '')
})

test('a mismatched account response is an authentication error', () => {
  assert.throws(
    () => validateDashboard({ user_id: 'bob' }, 'alice'),
    (error) => error instanceof ApiError && error.status === 401,
  )
})

test('private mutations bind both session and CSRF, preserve decimal text, and redact server errors', async () => {
  let options
  const api = createDataApi('/api/v1', async (_input, init) => {
    options = init
    return new Response(JSON.stringify({ message: 'synthetic-provider-secret-must-not-be-displayed' }), { status: 422 })
  })
  await assert.rejects(
    api.write(
      '/ledger/transactions',
      { quantity: '1.25000000', price: '1.20000000' },
      'csrf-example',
      'session-example',
    ),
    (error) => {
      assert.equal(error.status, 422)
      assert.doesNotMatch(error.message, /synthetic-provider-secret/)
      return true
    },
  )
  assert.equal(options.method, 'POST')
  assert.equal(options.headers['X-CSRF-Token'], 'csrf-example')
  assert.equal(options.headers['X-Session-Scope'], 'session-example')
  assert.deepEqual(JSON.parse(options.body), { quantity: '1.25000000', price: '1.20000000' })
})
