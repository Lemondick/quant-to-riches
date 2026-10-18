/* global document, innerWidth, window, MutationObserver, requestAnimationFrame */
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { mkdir, writeFile, open, cp } from 'node:fs/promises'
import { once } from 'node:events'
import net from 'node:net'
import { Agent, get } from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { verifyLiveWorkflows } from './verify-live-workflows.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const repo = path.resolve(root, '../..')
const output = path.join(repo, 'output/playwright/performance-live-20261017')
const workflowsOnly = process.argv.includes('--workflows-only')
await mkdir(output, { recursive: true })
const socket = net.createServer().listen(0, '127.0.0.1')
await once(socket, 'listening')
const port = socket.address().port
await new Promise((resolve) => socket.close(resolve))
const backend = `http://127.0.0.1:${port}`
const productionRoot = process.env.QUANT_LIVE_UI_BUILD || path.join(repo, 'output/performance-connected-build/build')
const workerSecret = `synthetic-local-${crypto.randomUUID()}-${crypto.randomUUID()}`
const dotnet = process.env.QUANT_DOTNET || 'C:/Users/admin/.dotnet/dotnet.exe'
const log = await open(path.join(output, 'api.log'), 'w')
const runtime = path.join(output, `runtime-${process.pid}`)
await cp(path.join(repo, 'web/backend-dotnet/src/QuantAssistant.Api/bin/Release/net10.0'), runtime, { recursive: true })
const api = spawn(dotnet, [path.join(runtime, 'QuantAssistant.Api.dll')], {
  cwd: path.join(repo, 'web/backend-dotnet/src/QuantAssistant.Api'),
  windowsHide: true,
  stdio: ['ignore', log.fd, log.fd],
  env: {
    ...process.env,
    QUANT_PG_CONNECTION: 'Host=127.0.0.1;Port=55439;Database=quant_perf_ui;Username=quant_test',
    ASPNETCORE_ENVIRONMENT: 'Development',
    ASPNETCORE_URLS: backend,
    QUANT_PUBLIC_REFRESH_ENABLED: 'false',
    QUANT_DISABLE_SCHEDULER: 'true',
    QUANT_TRACE_CONSOLE: '0',
    QUANT_WORKER_SECRET: workerSecret,
    QUANT_SITE_ROOT: path.join(output, 'site'),
    QUANT_SECRET_ROOT: path.join(output, 'private'),
    QUANT_WEB_ROOT: productionRoot,
  },
})
let browser
const httpAgent = new Agent({ keepAlive: true, maxSockets: 50, maxFreeSockets: 50 })
const results = [],
  errors = []
const passed = (name) => {
  results.push(name)
  console.log(`PASS ${name}`)
}
try {
  for (let attempt = 0; ; attempt++) {
    try {
      if ((await fetch(`${backend}/healthz`)).ok) break
    } catch {
      /* Wait for Kestrel. */
    }
    if (attempt > 150 || api.exitCode !== null) throw new Error('Temporary API did not become ready; see api.log')
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  const base = backend
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  const page = await context.newPage()
  page.setDefaultTimeout(20000)
  page.on('pageerror', (error) => errors.push(error.message))
  let summaries = 0
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/dashboard/summary') summaries++
  })
  await page.goto(base)
  await page.getByRole('form', { name: '登录后端账户' }).waitFor()
  await page.getByLabel('用户名', { exact: true }).fill('perf_ui_alice')
  await page.getByLabel('密码', { exact: true }).fill('SyntheticPerformancePassword123!')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('table', { name: '账户持有明细' }).getByText('Synthetic UI Fund', { exact: true }).waitFor()
  assert.equal(summaries, 1)
  assert.equal(await page.getByText('本地演示账户', { exact: true }).count(), 0)
  passed('Real login and one aggregate request render the account without sample fallback')
  await page.screenshot({ path: path.join(output, 'live-account.png'), fullPage: true })
  await page.locator('.side-nav').getByRole('link', { name: '我的自选', exact: true }).click()
  await page.getByRole('table', { name: '账户自选列表' }).getByText('Synthetic UI Fund', { exact: true }).waitFor()
  assert.equal(summaries, 1)
  await page.evaluate(() => {
    document.querySelector('.side-nav a[href="/"]').addEventListener(
      'click',
      () => {
        const began = performance.now()
        const observer = new MutationObserver(() => {
          if (!document.querySelector('table[aria-label="账户持有明细"]')?.textContent.includes('Synthetic UI Fund'))
            return
          observer.disconnect()
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              window.__quantCachedRouteMs = performance.now() - began
            }),
          )
        })
        observer.observe(document, { childList: true, subtree: true })
      },
      { once: true },
    )
  })
  await page.locator('.side-nav').getByRole('link', { name: '资产总览', exact: true }).click()
  await page.getByRole('table', { name: '账户持有明细' }).waitFor()
  await page.waitForFunction(() => window.__quantCachedRouteMs !== undefined)
  const cachedRouteMs = await page.evaluate(() => window.__quantCachedRouteMs)
  assert.equal(summaries, 1)
  passed('Route changes reuse the session-scoped summary without duplicate data requests')
  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  let held = false
  await page.route('**/api/v1/dashboard/summary', async (route) => {
    held = true
    await gate
    await route.continue()
  })
  await page.getByRole('button', { name: '刷新数据', exact: true }).click()
  await page.waitForFunction(() => document.body.innerText.includes('正在更新，当前显示最近一次有效数据。'))
  assert.equal(await page.getByRole('table', { name: '账户持有明细' }).count(), 1)
  assert.ok(held)
  release()
  await page.getByRole('button', { name: '刷新数据', exact: true }).waitFor()
  await page.unroute('**/api/v1/dashboard/summary')
  passed('A delayed real response keeps verified holdings visible during revalidation')
  await page.setViewportSize({ width: 390, height: 844 })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await page.screenshot({ path: path.join(output, 'live-mobile.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 960 })
  passed('Live account layout fits a mobile viewport')

  const benchmarks = {
    authenticated_cached_route: {
      duration_ms: cachedRouteMs,
      boundary:
        'Actual navigation click through two paint frames after cached owned rows render; session is revalidated',
    },
  }
  if (!workflowsOnly) {
    const firstScreens = []
    const firstScreenResources = []
    for (let trial = 0; trial < 5; trial++) {
      const coldContext = await browser.newContext({
        viewport: { width: 1440, height: 960 },
        storageState: { cookies: await context.cookies(), origins: [] },
      })
      const coldPage = await coldContext.newPage()
      await coldPage.addInitScript(() => {
        const observer = new MutationObserver(() => {
          if (!document.querySelector('table[aria-label="账户持有明细"]')?.textContent.includes('Synthetic UI Fund'))
            return
          observer.disconnect()
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              window.__quantDataReadyMs = performance.now()
            }),
          )
        })
        observer.observe(document, { childList: true, subtree: true })
      })
      await coldPage.goto(base, { waitUntil: 'domcontentloaded' })
      await coldPage.waitForFunction(() => window.__quantDataReadyMs !== undefined)
      firstScreens.push(await coldPage.evaluate(() => window.__quantDataReadyMs))
      firstScreenResources.push(
        await coldPage.evaluate(() =>
          performance.getEntriesByType('resource').map((item) => ({
            path: new URL(item.name).pathname,
            start_ms: item.startTime,
            duration_ms: item.duration,
            response_start_ms: item.responseStart,
            end_ms: item.responseEnd,
            bytes: item.transferSize,
          })),
        ),
      )
      await coldContext.close()
    }
    firstScreens.sort((left, right) => left - right)
    benchmarks.authenticated_cold_first_screen = {
      samples: firstScreens,
      median_ms: firstScreens[2],
      max_ms: firstScreens[4],
      resource_timings: firstScreenResources,
      boundary:
        'Production assets served by Kestrel on loopback in a fresh browser context with an existing authenticated cookie; navigation through two paint frames after real account rows render; excludes Caddy and TLS',
    }
    passed('Cold production first-screen timing is measured with empty browser caches')
    const cookies = await context.cookies(backend)
    const headers = { Cookie: cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; ') }
    async function benchmarkRequest(endpoint, identity = false) {
      const began = performance.now()
      const response = await fetch(backend + endpoint, {
        headers: identity ? { ...headers, 'Accept-Encoding': 'identity' } : headers,
      })
      assert.equal(response.status, 200)
      assert.match(response.headers.get('x-trace-id'), /^[0-9a-f]{32}$/)
      await response.arrayBuffer()
      const stages = Object.fromEntries(
        [...(response.headers.get('server-timing') || '').matchAll(/([a-z_]+);dur=([\d.]+)/g)].map((match) => [
          match[1],
          Number(match[2]),
        ]),
      )
      return { duration: performance.now() - began, stages, encoding: response.headers.get('content-encoding') }
    }
    function boundedHttpRequest(endpoint) {
      const began = performance.now()
      return new Promise((resolve, reject) => {
        const request = get(backend + endpoint, { headers, agent: httpAgent }, (response) => {
          response.on('error', reject)
          response.resume()
          response.on('end', () => {
            if (response.statusCode !== 200) {
              reject(new Error(`HTTP ${response.statusCode}`))
              return
            }
            assert.match(response.headers['x-trace-id'], /^[0-9a-f]{32}$/)
            resolve(performance.now() - began)
          })
        })
        request.on('error', reject)
        request.setTimeout(10000, () => request.destroy(new Error('HTTP benchmark timed out')))
      })
    }
    for (const endpoint of [
      '/api/v1/market/snapshot',
      '/api/v1/dashboard/summary',
      '/api/v1/market/quotes?indices=000300',
      '/api/v1/funds/valuations?symbols=510300',
      '/api/v1/funds/510300/detail',
      '/api/v1/market/news',
      '/api/v1/portfolio',
    ]) {
      await Promise.all(Array.from({ length: 50 }, () => benchmarkRequest(endpoint)))
      const records = await Promise.all(Array.from({ length: 50 }, () => benchmarkRequest(endpoint)))
      const durations = records.map((record) => record.duration)
      durations.sort((left, right) => left - right)
      const stageP95 = {}
      for (const name of new Set(records.flatMap((record) => Object.keys(record.stages)))) {
        const values = records
          .map((record) => record.stages[name])
          .filter(Number.isFinite)
          .sort((left, right) => left - right)
        stageP95[name] = values[Math.ceil(values.length * 0.95) - 1]
      }
      const sequential = []
      for (let index = 0; index < 10; index++) sequential.push((await benchmarkRequest(endpoint)).duration)
      sequential.sort((left, right) => left - right)
      let identityEncoding
      if (records.some((record) => record.encoding)) {
        const direct = await Promise.all(Array.from({ length: 50 }, () => benchmarkRequest(endpoint, true)))
        const values = direct.map((record) => record.duration).sort((left, right) => left - right)
        identityEncoding = { median_ms: values[24], p95_ms: values[47], max_ms: values[49] }
      }
      await Promise.all(Array.from({ length: 50 }, () => boundedHttpRequest(endpoint)))
      const bounded = (await Promise.all(Array.from({ length: 50 }, () => boundedHttpRequest(endpoint)))).sort(
        (left, right) => left - right,
      )
      benchmarks[endpoint] = {
        concurrency: 50,
        samples: 50,
        median_ms: durations[24],
        p95_ms: durations[47],
        max_ms: durations[49],
        server_stage_p95_ms: stageP95,
        sequential_samples: 10,
        sequential_median_ms: sequential[4],
        sequential_max_ms: sequential[9],
        content_encoding: [...new Set(records.map((record) => record.encoding))],
        identity_encoding_50_concurrent: identityEncoding,
        bounded_http_50: {
          driver: 'node:http, 50 persistent sockets, warm-up then one simultaneous burst',
          median_ms: bounded[24],
          p95_ms: bounded[47],
          max_ms: bounded[49],
        },
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 50))
    const metrics = await (await fetch(`${backend}/metrics`)).text()
    const queue = /quant_thread_pool_pending (\d+)/.exec(metrics)
    benchmarks.thread_pool_pending_after_load = queue ? Number(queue[1]) : null
    benchmarks.thread_pool_pending_peak_10s = Number(/quant_thread_pool_pending_peak_10s (\d+)/.exec(metrics)?.[1])
    benchmarks.thread_pool_sample_count_10s = Number(/quant_thread_pool_sample_count_10s (\d+)/.exec(metrics)?.[1])
    benchmarks.usage_counter_dropped = Number(/quant_usage_counter_dropped_total (\d+)/.exec(metrics)?.[1])
    assert.notEqual(benchmarks.thread_pool_pending_after_load, null)
    assert.ok(benchmarks.thread_pool_sample_count_10s > 0)
    assert.equal(benchmarks.usage_counter_dropped, 0)
    passed('Fifty concurrent real HTTP requests complete with trace IDs and thread-pool metrics')
    await writeFile(
      path.join(output, 'benchmark-evidence.json'),
      JSON.stringify({ fixture: 'Isolated quant_perf_ui synthetic fixtures', benchmarks }, null, 2),
    )
  }

  const beforeWorkflows = execFileSync(
    'uv',
    [
      'run',
      '--project',
      path.join(repo, 'web/backend'),
      'python',
      path.join(repo, 'web/backend-dotnet/tools/tests/seed_performance_ui.py'),
      '--check',
    ],
    { encoding: 'utf8', windowsHide: true },
  )
  assert.equal(JSON.parse(beforeWorkflows.trim()).compute_jobs, 0)
  await verifyLiveWorkflows({ page, context, base, backend, repo, output, passed, workerSecret })

  const bob = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: 'perf_ui_bob', password: 'SyntheticPerformancePassword123!' },
  })
  assert.equal(bob.status(), 200)
  const bobSession = await bob.json()
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await page
    .getByText(/2,000\.00/)
    .first()
    .waitFor()
  assert.equal(await page.getByText('Synthetic UI Fund', { exact: true }).count(), 0)
  passed('Changing the actual backend session removes the previous account cache')
  const logout = await context.request.post(`${base}/api/v1/auth/logout`, {
    headers: { 'X-CSRF-Token': bobSession.csrf_token },
  })
  assert.equal(logout.status(), 200)
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await page.getByRole('form', { name: '登录后端账户' }).waitFor()
  assert.equal(await page.getByRole('table', { name: '账户持有明细' }).count(), 0)
  passed('An expired or logged-out session clears private data and offers real login')
  assert.deepEqual(errors, [])
  const check = execFileSync(
    'uv',
    [
      'run',
      '--project',
      path.join(repo, 'web/backend'),
      'python',
      path.join(repo, 'web/backend-dotnet/tools/tests/seed_performance_ui.py'),
      '--check',
    ],
    { encoding: 'utf8', windowsHide: true },
  )
  const jobs = JSON.parse(check.trim())
  assert.ok(jobs.compute_jobs > 0)
  assert.deepEqual(Object.keys(jobs.kinds).sort(), ['strategy.preview', 'strategy.view'])
  await writeFile(
    path.join(output, workflowsOnly ? 'workflow-evidence.json' : 'evidence.json'),
    JSON.stringify(
      { fixture: 'Synthetic data only in the isolated quant_perf_ui database', results, benchmarks, jobs, errors },
      null,
      2,
    ),
  )
  console.log(JSON.stringify({ benchmarks, jobs }, null, 2))
} catch (error) {
  console.error(error)
  if (browser) {
    const pages = browser.contexts().flatMap((context) => context.pages())
    await pages
      .at(-1)
      ?.screenshot({ path: path.join(output, 'failure.png'), fullPage: true })
      .catch(() => {})
  }
  throw error
} finally {
  await browser?.close()
  httpAgent.destroy()
  if (api.exitCode === null) {
    api.kill()
    await once(api, 'exit')
  }
  await log.close()
}
