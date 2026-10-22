import test from 'node:test'
import assert from 'node:assert/strict'
import {
  copyModelConfiguration,
  defaultModelConfiguration,
  normalizeApiBaseUrl,
  validateModelConfiguration,
} from '../src/utils/modelConfig.ts'
import { createModelConfigApi } from '../src/services/modelConfig.ts'

test('model configuration validates roles and keeps credentials out of persisted data', () => {
  const config = copyModelConfiguration({
    ...defaultModelConfiguration,
    apiKey: 'secret',
    models: { ...defaultModelConfiguration.models, apiKey: 'secret' },
  })
  assert.deepEqual(Object.keys(config), ['baseUrl', 'models'])
  assert.deepEqual(Object.keys(config.models), ['news', 'strategy', 'risk', 'jev'])
  assert.ok(!JSON.stringify(config).includes('secret'))
  assert.equal(validateModelConfiguration(config), '')
  config.models.risk = ' '
  assert.match(validateModelConfiguration(config), /风险复核模型/)
  config.models.risk = 'a'.repeat(201)
  assert.match(validateModelConfiguration(config), /200/)
})

test('API URLs preserve provider paths and reject credentials or unsafe protocols', () => {
  assert.equal(normalizeApiBaseUrl(' https://example.com/custom/v1/ '), 'https://example.com/custom/v1')
  assert.equal(normalizeApiBaseUrl('http://127.0.0.1:8000/v1'), 'http://127.0.0.1:8000/v1')
  for (const url of [
    'javascript:alert(1)',
    'file:///test',
    'https://name:secret@example.com',
    'https://example.com?api_key=secret',
    'https://example.com#secret',
    'invalid',
  ]) {
    assert.throws(() => normalizeApiBaseUrl(url))
    assert.ok(validateModelConfiguration({ ...defaultModelConfiguration, baseUrl: url }))
  }
})

test('model discovery uses backend POST bodies, deduplicates results and supports stored credentials', async () => {
  const calls = []
  const api = createModelConfigApi('/api/', async (url, init) => {
    calls.push({ url, init })
    return Response.json({ models: [' model-b ', 'model-a', 'model-b'] })
  })
  assert.deepEqual(await api.connectCompatible('https://example.com/v1', 'test-key'), ['model-a', 'model-b'])
  assert.equal(calls[0].url, '/api/model-config/connection')
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[0].init.redirect, 'error')
  assert.deepEqual(JSON.parse(calls[0].init.body), { baseUrl: 'https://example.com/v1', apiKey: 'test-key' })
  assert.ok(!calls[0].url.includes('test-key'))
  await api.connectJev('')
  assert.equal(calls[1].url, '/api/model-config/jev-connection')
  assert.deepEqual(JSON.parse(calls[1].init.body), {})
})

test('configuration reads strip backend secrets and saves never send credentials', async () => {
  const calls = []
  const api = createModelConfigApi('https://backend.example/api', async (url, init) => {
    calls.push({ url, init })
    return init.method === 'PUT'
      ? new Response(null, { status: 204 })
      : Response.json({
          ...defaultModelConfiguration,
          apiKey: 'server-secret',
          connections: { compatible: true, jev: false },
        })
  })
  const remote = await api.read()
  assert.deepEqual(remote.connections, { compatible: true, jev: false })
  assert.ok(!JSON.stringify(remote.configuration).includes('server-secret'))
  await api.save({ ...remote.configuration, apiKey: 'client-secret' })
  assert.ok(!calls[1].init.body.includes('client-secret'))
  await api.defaults()
  assert.equal(calls[2].url, 'https://backend.example/api/model-config/defaults')
})

test('backend failures remain explicit and never echo response credentials', async () => {
  for (const status of [401, 403, 404, 429, 500]) {
    const api = createModelConfigApi('/api', async () => new Response('server echoed secret-key', { status }))
    await assert.rejects(
      api.connectJev('secret-key'),
      (error) => !error.message.includes('secret-key') && error.message.length > 0,
    )
  }
  const malformed = createModelConfigApi('/api', async () => new Response('<html>preview fallback</html>'))
  await assert.rejects(malformed.read(), /JSON/)
  const invalid = createModelConfigApi('/api', async () => Response.json({ models: [null] }))
  await assert.rejects(invalid.connectJev('key'), /格式/)
  const empty = createModelConfigApi('/api', async () => Response.json({ models: [] }))
  await assert.rejects(empty.connectJev('key'), /没有可用模型/)
  const unsafe = createModelConfigApi('/api', async () =>
    Response.json({ ...defaultModelConfiguration, baseUrl: 'https://user:secret@example.com' }),
  )
  await assert.rejects(unsafe.read(), /无效地址/)
  const offline = createModelConfigApi('', async () => assert.fail('unconfigured service must not request'))
  await assert.rejects(offline.read(), /尚未接入/)
})

test('leaving the view cancels backend discovery without reporting a successful connection', async () => {
  const controller = new AbortController()
  const api = createModelConfigApi(
    '/api',
    async (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
      }),
  )
  const pending = api.connectJev('test-key', controller.signal)
  controller.abort()
  await assert.rejects(pending, (error) => /已取消/.test(error.message) && error.cause?.name === 'AbortError')
})

test('model connection errors retain the transport cause behind the user-facing message', async () => {
  const transport = new TypeError('synthetic socket closed')
  const api = createModelConfigApi('/api', async () => {
    throw transport
  })
  await assert.rejects(api.read(), (error) => /无法连接后端服务/.test(error.message) && error.cause === transport)
})
