/* global document, window, getComputedStyle, location, requestAnimationFrame, innerWidth */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const repo = path.resolve(root, '../..')
const stage = process.env.QUANT_UI_CHECK_STAGE || 'after'
const build = process.env.QUANT_UI_CHECK_BUILD || path.join(repo, 'output/ui-responsive-v12/build')
const output = path.join(repo, 'output/playwright/ui-responsive-20261017', stage)
await mkdir(output, { recursive: true })
const server = await preview({ root, build: { outDir: build }, preview: { host: '127.0.0.1', port: 0 } })
const base = `http://127.0.0.1:${server.httpServer.address().port}`
const routes = [
  ['/', '资产总览', 'table[aria-label="账户持有明细"]'],
  ['/portfolio', '组合分析', '.kpi-grid .metric-card:last-child'],
  ['/ledger', '交易账本', 'table[aria-label="账户交易账本"]'],
  ['/watchlist', '我的自选', 'table[aria-label="账户自选列表"]'],
  ['/market', '市场行情', '.index-strip .index-card:first-child'],
  ['/ai-stock', 'AI选股', '.ai-hero'],
  ['/fund-review', '基金复核', '.kpi-grid .metric-card:last-child'],
  ['/quant', '量化策略', '.strategy-cards'],
  ['/news', '市场资讯', '.news-card'],
  ['/admin', '管理控制台', '.stats-grid .stat-card:last-child'],
  ['/settings', '账号与设置', '.setting-grid'],
]
const errors = [],
  layouts = [],
  timings = []
const apiReads = []
let browser
let emptyAccount = false,
  partialIndices = false
async function contextFor(delayModules = false) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, reducedMotion: 'no-preference' })
  await context.route(base + '/api/**', (route) => {
    assert.equal(route.request().method(), 'GET')
    const url = new URL(route.request().url())
    apiReads.push(url.pathname)
    const data = structuredClone(responseFor(url))
    if (emptyAccount && url.pathname.endsWith('/dashboard/summary')) {
      data.portfolio.positions = []
      data.preferences.watchlist = []
      data.valuations.items = []
    }
    if (partialIndices && url.pathname.endsWith('/market/snapshot')) data.indices = data.indices.slice(0, 3)
    return route.fulfill({ json: data })
  })
  if (delayModules)
    await context.route(base + '/assets/**', async (route) => {
      if (new URL(route.request().url()).pathname.endsWith('.js'))
        await new Promise((resolve) => setTimeout(resolve, 100))
      return route.continue()
    })
  return context
}
try {
  browser = await chromium.launch({ headless: true })
  const context = await contextFor()
  const page = await context.newPage()
  page.on('pageerror', (error) => errors.push(error.message))
  for (const width of [320, 390, 768, 1024, 1440, 1920, 2323, 2560]) {
    await page.setViewportSize({ width, height: width < 640 ? 844 : 1080 })
    for (const [route, title, ready] of routes) {
      await page.goto(base + route)
      await page.getByRole('heading', { level: 1, name: title, exact: true }).waitFor()
      await page.locator(ready).waitFor()
      const layout = await page.evaluate(() => {
        const grids = [...document.querySelectorAll('.kpi-grid,.stats-grid,.cards-list')].map((node) => {
          const box = node.getBoundingClientRect(),
            children = [...node.children].map((child) => child.getBoundingClientRect())
          const oneRow = children.length > 0 && children.every((child) => Math.abs(child.y - children[0].y) < 1)
          return {
            className: node.className,
            width: box.width,
            children: children.length,
            oneRow,
            unusedRight: oneRow ? box.right - children.at(-1).right : 0,
          }
        })
        return { viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth, grids }
      })
      layouts.push({ route, width, ...layout })
      if (stage !== 'before') {
        assert.ok(layout.scrollWidth <= width + 1, `${width}px ${route}: document overflow`)
        for (const grid of layout.grids)
          assert.ok(grid.unusedRight < 2, `${width}px ${route}: ${grid.className} leaves ${grid.unusedRight}px unused`)
      }
    }
  }
  for (const width of [390, 1920, 2323, 2560]) {
    emptyAccount = true
    await page.setViewportSize({ width, height: width < 640 ? 844 : 1080 })
    await page.goto(base + '/fund-review')
    await page.locator('.kpi-grid .metric-card').last().waitFor()
    await page.waitForTimeout(stage === 'before' ? 750 : 50)
    await page.screenshot({ path: path.join(output, `fund-review-empty-${width}.png`), fullPage: true })
  }
  emptyAccount = false
  partialIndices = true
  await page.setViewportSize({ width: 2323, height: 1080 })
  await page.goto(base + '/market')
  await page.locator('.stat-card').nth(2).waitFor()
  const partialState = {
    cards: await page.locator('[data-index-id]').count(),
    available: (await page.locator('[data-index-id] .stat-value').allTextContents()).filter((value) => value !== '—')
      .length,
  }
  if (stage !== 'before') {
    assert.equal(partialState.cards, 15, 'Registry keeps independent unavailable slots')
    const values = await page.locator('[data-index-id] .stat-value').allTextContents()
    assert.equal(values.filter((value) => value !== '—').length, 3, 'Three actual quotes remain independently visible')
  }
  partialIndices = false
  await context.close()

  const timingContext = await contextFor(true)
  const timingPage = await timingContext.newPage()
  timingPage.on('pageerror', (error) => errors.push(error.message))
  await timingPage.goto(base)
  await timingPage.locator('table[aria-label="账户持有明细"]').waitFor()
  await timingPage.waitForTimeout(100)
  const readsBeforeIdle = apiReads.length
  await timingPage.waitForTimeout(2500)
  const idleBusinessReads = apiReads.length - readsBeforeIdle
  assert.equal(idleBusinessReads, 0, 'Preloading must not execute account reads or business jobs')
  const loadedBeforeClick = await timingPage.evaluate(() =>
    performance.getEntriesByType('resource').some((item) => /LiveFundReview-.*\.js/.test(item.name)),
  )
  async function navigate(target, label = 'warm') {
    const [route, title, selector] = target
    await timingPage.evaluate(
      ({ route, title, selector }) => {
        const link = document.querySelector('.sidebar a[href="' + route + '"]')
        window.__routePaint = undefined
        link.addEventListener(
          'click',
          () => {
            const result = (window.__routePaint = { route, started: performance.now() })
            function painted(node) {
              if (!node || node.getBoundingClientRect().width === 0) return false
              for (let current = node; current; current = current.parentElement)
                if (Number(getComputedStyle(current).opacity) < 0.98) return false
              return true
            }
            function tick() {
              if (location.pathname === route && document.querySelector('h1')?.textContent === title) {
                if (!result.headerPending && painted(document.querySelector('h1'))) {
                  result.headerPending = true
                  requestAnimationFrame(() =>
                    requestAnimationFrame(() => {
                      result.headerMs = performance.now() - result.started
                    }),
                  )
                }
                if (!result.contentPending && painted(document.querySelector(selector))) {
                  result.contentPending = true
                  requestAnimationFrame(() =>
                    requestAnimationFrame(() => {
                      result.contentMs = performance.now() - result.started
                    }),
                  )
                }
              }
              if (result.contentMs === undefined || result.headerMs === undefined) requestAnimationFrame(tick)
            }
            requestAnimationFrame(tick)
          },
          { once: true, capture: true },
        )
      },
      { route, title, selector },
    )
    await timingPage
      .locator('.sidebar')
      .getByRole('link', { name: route === '/admin' ? '管理者后台' : title, exact: true })
      .click()
    await timingPage.waitForFunction(
      () => window.__routePaint?.contentMs !== undefined && window.__routePaint?.headerMs !== undefined,
    )
    const result = await timingPage.evaluate(() => ({
      route: window.__routePaint.route,
      headerMs: window.__routePaint.headerMs,
      contentMs: window.__routePaint.contentMs,
    }))
    timings.push({ label, ...result })
  }
  await navigate(
    routes.find((item) => item[0] === '/fund-review'),
    'first visit after idle',
  )
  const sequence = [
    '/watchlist',
    '/portfolio',
    '/market',
    '/ledger',
    '/news',
    '/admin',
    '/settings',
    '/fund-review',
    '/',
  ]
  for (let round = 0; round < 3; round++)
    for (const route of sequence) await navigate(routes.find((item) => item[0] === route))
  const warm = timings.filter((item) => item.label === 'warm')
  const percentile = (values) => [...values].sort((a, b) => a - b)[Math.ceil(values.length * 0.95) - 1]
  const summary = {
    header_p95_ms: percentile(warm.map((item) => item.headerMs)),
    content_p95_ms: percentile(warm.map((item) => item.contentMs)),
  }
  if (stage !== 'before') {
    assert.ok(loadedBeforeClick, 'Idle loading must prepare an unvisited route')
    assert.ok(summary.header_p95_ms < 150, JSON.stringify(summary))
    assert.ok(summary.content_p95_ms < 200, JSON.stringify(summary))
  }
  assert.deepEqual(errors, [])
  await writeFile(
    path.join(output, 'evidence.json'),
    JSON.stringify(
      {
        stage,
        build,
        fixture_scope:
          'Read-only local fixtures; normal motion; 100ms delay for JS module requests; excludes public API/network latency',
        layouts,
        partial_indices: partialState,
        idle_business_reads: idleBusinessReads,
        loaded_before_first_click: loadedBeforeClick,
        timings,
        summary,
        errors,
      },
      null,
      2,
    ),
  )
  console.log(
    JSON.stringify({ stage, layouts: layouts.length, loadedBeforeClick, ...summary, first_visit: timings[0] }),
  )
  await timingContext.close()
} finally {
  await browser?.close()
  await server.httpServer.close()
}
