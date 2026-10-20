/* global document, innerWidth */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { indexRegistry, responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url)),
  repo = path.resolve(root, '../..')
const output = path.join(repo, 'output/playwright/market-kline-20261018')
await mkdir(output, { recursive: true })
const server = await preview({
  root,
  build: { outDir: process.env.QUANT_KLINE_BUILD || path.join(repo, 'output/kline-v14/ui/build') },
  preview: { host: '127.0.0.1', port: 0 },
})
const base = `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ headless: true })
const errors = [],
  checks = [],
  timings = []
const days = Array.from({ length: 220 }, (_, i) =>
  new Date(Date.UTC(2026, 0, 1) + i * 86400000).toISOString().slice(0, 10),
)
try {
  for (const width of [320, 390, 1440, 2323]) {
    let fundCalls = 0,
      historyCalls = 0
    const context = await browser.newContext({ viewport: { width, height: 1080 }, reducedMotion: 'no-preference' })
    await context.route(base + '/api/**', async (route) => {
      assert.equal(route.request().method(), 'GET')
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/market/history')) {
        if (++historyCalls === 1)
          return route.fulfill({
            status: 503,
            json: { error_code: 'public_snapshot_unavailable', message: 'preparing' },
          })
        const definition = indexRegistry.find(
          (row) =>
            row.id === url.searchParams.get('index_id') ||
            (row.legacy_history_symbol && row.legacy_history_symbol === url.searchParams.get('symbol')),
        )
        assert.ok(definition)
        const symbol = definition.exchange_code
        return route.fulfill({
          json: {
            id: definition.id,
            symbol,
            name: symbol === '000300' ? '沪深300' : '上证指数',
            source: 'explicit-local-layout-fixture',
            stale: historyCalls === 2,
            observed_at: '2026-10-17T00:00:00Z',
            items: days.map((date, i) => ({
              date,
              open: String(3000 + i),
              high: String(3010 + i),
              low: String(2990 + i),
              close: String(3005 + i),
              volume: String(100 + i),
            })),
          },
        })
      }
      if (/\/funds\/\d{6}\/detail$/.test(url.pathname)) {
        if (++fundCalls < 3)
          return route.fulfill({
            status: 503,
            json: { error_code: 'public_snapshot_unavailable', message: 'preparing' },
          })
        return route.fulfill({
          json: {
            symbol: url.pathname.split('/')[4],
            name: '隔离验收基金',
            fund_type: '指数型',
            company: '本地夹具',
            observed_at: '2026-10-17T00:00:00Z',
            stale: false,
            nav_history: days.map((nav_date, i) => ({ nav_date, unit_nav: String(1 + i / 1000) })),
          },
        })
      }
      return route.fulfill({ json: responseFor(url) })
    })
    const page = await context.newPage()
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto(base + '/market')
    const index = page.locator('.candle-card[data-symbol="cn.csi.300"]')
    await index.locator('.terminal-canvas canvas').first().waitFor()
    await page.waitForFunction(
      () =>
        !document
          .querySelector('.candle-card[data-symbol="cn.csi.300"] .chart-caption')
          ?.textContent.includes('最近有效快照'),
      undefined,
      { timeout: 5000 },
    )
    assert.equal(await page.locator('[role="alert"]').count(), 0, 'Preparing data must not flash a red error')
    for (const [label, period] of [
      ['日K', 'day'],
      ['周K', 'week'],
      ['月K', 'month'],
      ['年K', 'year'],
    ]) {
      await index.getByRole('button', { name: label, exact: true }).click()
      assert.equal(await index.locator('.terminal-canvas').getAttribute('data-period'), period)
    }
    await index.getByRole('button', { name: '日K', exact: true }).click()
    const host = index.locator('.terminal-canvas')
    await host.focus()
    await host.press('Home')
    assert.equal(await host.getAttribute('data-inspected-index'), '0')
    await host.press('End')
    assert.equal(await host.getAttribute('data-inspected-index'), '219')
    await index.getByRole('button', { name: '放大K线', exact: true }).click()
    await index.getByRole('button', { name: '缩小K线', exact: true }).click()
    await index.getByRole('button', { name: 'MACD', exact: true }).click()
    assert.equal(await index.getByRole('button', { name: 'MACD', exact: true }).getAttribute('aria-pressed'), 'false')
    await index.getByRole('button', { name: 'MACD', exact: true }).click()
    await index.getByRole('combobox', { name: '选择指数' }).selectOption('cn.sse.composite')
    await page.locator('.candle-card[data-symbol="cn.sse.composite"] .terminal-canvas canvas').first().waitFor()
    checks.push(width + ': real OHLC, periods, keyboard, zoom, indicator and index change')
    await page.getByRole('button', { name: '基金榜', exact: true }).click()
    const fundLink = page.getByRole('button', { name: '布局验收基金', exact: true })
    // Click without hovering first to exercise a truly cold fund selection.
    const started = performance.now()
    await fundLink.evaluate((node) => node.click())
    const fund = page.getByRole('region', { name: '基金公开资料' })
    await fund.locator('.terminal-canvas canvas').first().waitFor()
    timings.push({ width, cold_preparing_to_chart_ms: performance.now() - started })
    assert.equal(await page.locator('[role="alert"]').count(), 0)
    assert.match(await fund.innerText(), /净值K线/)
    assert.equal(fundCalls, 3, 'Preparing reads must retry and share one resource')
    const loaded = fundCalls
    const again = performance.now()
    await fundLink.click()
    await fund.locator('.terminal-canvas canvas').first().waitFor()
    assert.equal(fundCalls, loaded, 'Repeat opening reuses observed history')
    timings.push({ width, cached_repeat_ms: performance.now() - again })
    const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }))
    assert.ok(geometry.scroll <= width + 1, 'No horizontal overflow')
    checks.push(
      width + ': initial preparation without false error, real NAV candles, cached repeat and responsive layout',
    )
    await page.screenshot({ path: path.join(output, 'fund-' + width + '.png'), fullPage: true })
    await context.close()
  }
  assert.deepEqual(errors, [])
  await writeFile(
    path.join(output, 'evidence.json'),
    JSON.stringify(
      {
        scope: 'Read-only local fixtures with native 503 preparation responses; production accounts untouched',
        checks,
        timings,
        errors,
      },
      null,
      2,
    ),
  )
  console.log(JSON.stringify({ checks: checks.length, timings, errors }))
} finally {
  await browser.close()
  await server.httpServer.close()
}
