import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { createDataApi } from '../src/services/dataApi.ts'

function serviceWorker() {
  const listeners = {},
    stores = new Map(),
    deleted = []
  let online = true,
    user = 'alice',
    status = 200
  const keyOf = (request) => (typeof request === 'string' ? request : request.url)
  const caches = {
    keys: async () => [...stores.keys()],
    delete: async (key) => {
      deleted.push(key)
      return stores.delete(key)
    },
    open: async (key) => {
      if (!stores.has(key)) stores.set(key, new Map())
      const entries = stores.get(key)
      return {
        addAll: async (paths) => {
          for (const path of paths) entries.set(path, new Response('shell'))
        },
        match: async (request) => entries.get(keyOf(request))?.clone(),
        put: async (request, response) => {
          entries.set(keyOf(request), response.clone())
        },
      }
    },
  }
  const network = async () => {
    if (!online) throw new Error('offline')
    return new Response(JSON.stringify({ user }), { status, headers: { 'Cache-Control': 'no-store' } })
  }
  vm.runInNewContext(
    readFileSync(new URL('../src/pwa/sw.template.js', import.meta.url), 'utf8').replaceAll(
      '__GUANLAN_PRECACHE_ASSETS__',
      '["/", "/assets/app.js"]',
    ),
    {
      self: {
        addEventListener: (name, listener) => {
          listeners[name] = listener
        },
        location: { origin: 'https://quant.test' },
        skipWaiting: async () => {},
        clients: { claim: async () => {}, matchAll: async () => [] },
      },
      caches,
      fetch: network,
      URL,
      Response,
      Date,
      Promise,
    },
  )
  return {
    stores,
    deleted,
    caches,
    async lifecycle(name) {
      let pending
      listeners[name]({
        waitUntil: (value) => {
          pending = value
        },
      })
      await pending
    },
    async get(path, options = {}) {
      const waits = []
      let handled
      const request = { url: 'https://quant.test' + path, method: 'GET', mode: options.navigate ? 'navigate' : 'cors' }
      listeners.fetch({
        request,
        respondWith: (value) => {
          handled = value
        },
        waitUntil: (value) => waits.push(value),
      })
      const response = await (handled ?? network(request))
      await Promise.all(waits)
      return response
    },
    setNetwork(next) {
      ;({ online = online, user = user, status = status } = next)
    },
  }
}

test('service worker never reuses a private session after account switch or logout', async () => {
  const sw = serviceWorker()
  assert.deepEqual(await (await sw.get('/api/v1/auth/session')).json(), { user: 'alice' })
  sw.setNetwork({ user: 'bob' })
  assert.deepEqual(await (await sw.get('/api/v1/auth/session')).json(), { user: 'bob' })
  sw.setNetwork({ status: 401, user: null })
  assert.equal((await sw.get('/api/v1/auth/session')).status, 401)
  assert.equal(
    [...sw.stores.values()].reduce(
      (count, entries) => count + [...entries.keys()].filter((key) => key.includes('/api/v1')).length,
      0,
    ),
    0,
  )
})

test('activation purges old private API caches and retains the offline shell', async () => {
  const sw = serviceWorker()
  await sw.caches.open('guanlan-api-v1')
  await sw.caches.open('guanlan-api-v2')
  await sw.lifecycle('install')
  await sw.lifecycle('activate')
  assert.ok(sw.deleted.includes('guanlan-api-v1'))
  assert.ok(sw.deleted.includes('guanlan-api-v2'))
  sw.setNetwork({ online: false })
  assert.equal(await (await sw.get('/goals', { navigate: true })).text(), 'shell')
  await assert.rejects(sw.get('/api/v1/portfolio'), /offline/)
  await assert.rejects(sw.get('/api/v1', { navigate: true }), /offline/)
})

test('API console snapshot routes reach one API prefix for reads and writes', async () => {
  const urls = []
  const api = createDataApi('https://quant.test', async (url) => {
    urls.push(url)
    return Response.json({ ok: true })
  })
  await api.read('/api/v1/admin/users')
  await api.write('/api/v1/goals/g1', {}, 'csrf', 'scope', 'PUT')
  assert.deepEqual(urls, ['https://quant.test/api/v1/admin/users', 'https://quant.test/api/v1/goals/g1'])
})

test('password challenge and OTP or recovery code use the anonymous verification endpoint', async () => {
  const calls = []
  const api = createDataApi('https://quant.test', async (url, init) => {
    calls.push({ url, method: init.method, body: JSON.parse(init.body), csrf: init.headers['X-CSRF-Token'] })
    return Response.json(
      url.endsWith('/login')
        ? {
            two_factor_required: true,
            pending_token: 'challenge',
            error_code: 'two_factor_required',
            message: '需要双重验证',
          }
        : {
            user: { id: 'u1', username: 'alice', role: 'user' },
            csrf_token: 'csrf',
            expires_at: '2026-10-23T00:00:00Z',
          },
    )
  })
  const challenge = await api.login('alice', 'password')
  assert.equal(challenge.two_factor_required, true)
  await api.verifyTwoFactor(challenge.pending_token, '123456')
  await api.verifyTwoFactor(challenge.pending_token, 'ABCD-EFGH')
  assert.deepEqual(calls.slice(1), [
    {
      url: 'https://quant.test/api/v1/auth/2fa/verify',
      method: 'POST',
      body: { pending_token: 'challenge', code: '123456' },
      csrf: undefined,
    },
    {
      url: 'https://quant.test/api/v1/auth/2fa/verify',
      method: 'POST',
      body: { pending_token: 'challenge', code: 'ABCD-EFGH' },
      csrf: undefined,
    },
  ])
})
