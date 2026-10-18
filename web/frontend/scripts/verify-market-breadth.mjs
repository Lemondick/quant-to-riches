/* global document, innerWidth */
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url)),
  repo = path.resolve(root, '../..')
const output = path.join(repo, 'output/playwright/market-breadth-20261018', process.env.QUANT_BREADTH_STAGE || 'after')
await mkdir(output, { recursive: true })
const server = process.env.QUANT_BREADTH_URL
  ? undefined
  : await preview({
      root,
      build: { outDir: process.env.QUANT_BREADTH_BUILD || path.join(repo, 'output/breadth-v16/ui/build') },
      preview: { host: '127.0.0.1', port: 0 },
    })
const base = process.env.QUANT_BREADTH_URL || `http://127.0.0.1:${server.httpServer.address().port}`
const live = process.env.QUANT_BREADTH_DATA
  ? JSON.parse(await readFile(process.env.QUANT_BREADTH_DATA, 'utf8'))
  : undefined
const sample = live?.breadth || {
  advancing: 3061,
  declining: 2023,
  unchanged: 132,
  limit_up: 61,
  limit_down: 11,
  observed_at: '2026-10-16T15:00:00+08:00',
  source: 'explicit-local-layout-fixture',
  turnover: '1555420779651.99',
  previous_turnover: '1633004548031.84',
  turnover_ratio: '0.9524901706137074107667317759',
  previous_trade_date: '2026-10-15',
  stale: false,
  unavailable: [],
}
const browser = await chromium.launch({ headless: true }),
  errors = [],
  checks = []
try {
  for (const width of [320, 390, 1440, 2560]) {
    for (const state of ['ready', 'zero', 'partial', 'stale', 'unavailable']) {
      const context = await browser.newContext({ viewport: { width, height: 1080 } })
      await context.route(base + '/api/**', (route) => {
        assert.equal(route.request().method(), 'GET')
        const url = new URL(route.request().url()),
          data = structuredClone(responseFor(url))
        if (url.pathname.endsWith('/market/snapshot')) {
          data.breadth = structuredClone(sample)
          if (state === 'unavailable') data.breadth = null
          if (state === 'stale') data.breadth.stale = true
          if (state === 'zero') Object.assign(data.breadth, { advancing: 0, limit_up: 0 })
          if (state === 'partial')
            Object.assign(data.breadth, {
              turnover: null,
              previous_turnover: null,
              turnover_ratio: null,
              unavailable: ['turnover'],
            })
        }
        return route.fulfill({ json: data })
      })
      const page = await context.newPage()
      page.on('pageerror', (error) => errors.push(error.message))
      await page.goto(base + '/market')
      const card = page.locator('.market-breadth-card')
      await card.waitFor()
      if (state !== 'unavailable') {
        await card
          .getByText(`上涨 ${state === 'zero' ? '0' : '3,061'}`, { exact: true })
          .waitFor({ state: state === 'zero' ? 'attached' : 'visible', timeout: 4000 })
        await card.getByText('下跌 2,023', { exact: true }).waitFor()
        assert.ok((await card.innerText()).includes('2026-10-16 15:00'))
        assert.ok((await card.innerText()).includes('平盘 132'))
        assert.equal(await card.locator('.breadth-stat b').first().innerText(), state === 'zero' ? '0' : '61')
        assert.equal(await card.locator('.breadth-stat b').nth(1).innerText(), '11')
        assert.equal(await card.locator('.breadth-stat b').nth(2).innerText(), state === 'partial' ? '—' : '1.56 万亿')
        assert.equal(await card.locator('.breadth-stat b').nth(3).innerText(), state === 'partial' ? '—' : '0.95 倍')
        if (state === 'stale') assert.ok((await card.innerText()).includes('最近有效统计'))
      } else {
        assert.equal(
          await card
            .locator('.breadth-stat b')
            .allTextContents()
            .then((items) => items.join('')),
          '————',
        )
        assert.equal(await card.getByText('上涨 0', { exact: true }).count(), 0)
      }
      await page.locator('.two-col .terminal-canvas canvas').first().waitFor()
      await page.waitForTimeout(200)
      const geometry = await page.locator('.two-col').evaluate((grid) => ({
        overflow: document.documentElement.scrollWidth > innerWidth,
        gap:
          grid.querySelector('.candle-card').getBoundingClientRect().bottom -
          grid.querySelector('.market-side-panels').lastElementChild.getBoundingClientRect().bottom,
      }))
      assert.equal(geometry.overflow, false)
      if (width > 1200) assert.ok(Math.abs(geometry.gap) <= 1)
      checks.push({ width, state, ...geometry })
      if (state === 'ready') await card.screenshot({ path: path.join(output, `breadth-${width}.png`) })
      await context.close()
    }
  }
  assert.deepEqual(errors, [])
  await writeFile(
    path.join(output, 'results.json'),
    JSON.stringify(
      {
        base,
        scope: 'Real UI, read-only intercepted account requests; public breadth replay when supplied',
        checks,
        errors,
      },
      null,
      2,
    ) + '\n',
  )
  console.log(JSON.stringify({ checks: checks.length, errors }))
} finally {
  await browser.close()
  await server?.close()
}
