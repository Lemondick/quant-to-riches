/* global document, innerWidth */
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const repo = path.resolve(root, '../..')
const stage = process.env.QUANT_ALIGNMENT_STAGE || 'after'
const build = process.env.QUANT_ALIGNMENT_BUILD || path.join(repo, 'output/market-alignment-v15/build')
const output = path.join(repo, 'output/playwright/market-alignment-20261018', stage)
const histories = JSON.parse(await readFile(path.join(repo, 'output/kline-v14/public-chart-data.json'), 'utf8'))
await mkdir(output, { recursive: true })
const server = process.env.QUANT_ALIGNMENT_URL
  ? undefined
  : await preview({ root, build: { outDir: build }, preview: { host: '127.0.0.1', port: 0 } })
const base = process.env.QUANT_ALIGNMENT_URL || `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ headless: true })
const errors = [],
  checks = []

async function measure(page, width, phase) {
  const result = await page.locator('.two-col').evaluate((grid) => {
    const rect = (element) => {
      const { x, y, width, height, bottom, right } = element.getBoundingClientRect()
      return { x, y, width, height, bottom, right }
    }
    const side = grid.querySelector('.market-side-panels')
    return {
      chart: rect(grid.querySelector('.candle-card')),
      breadth: rect(side.firstElementChild),
      industry: rect(side.lastElementChild),
      rows: [...side.querySelectorAll('.row')].map(rect),
      overflow: document.documentElement.scrollWidth > innerWidth,
    }
  })
  const bottomGap = result.chart.bottom - result.industry.bottom
  checks.push({ width, phase, bottomGap, ...result })
  assert.equal(result.overflow, false, `Horizontal overflow at ${width}px (${phase})`)
  if (width > 1200) {
    assert.ok(Math.abs(result.chart.y - result.breadth.y) <= 1, `Unequal card tops at ${width}px`)
    if (stage !== 'before') assert.ok(Math.abs(bottomGap) <= 1, `Unequal card bottoms at ${width}px: ${bottomGap}px`)
    assert.ok(
      result.rows.every((row) => row.bottom <= result.industry.bottom),
      'Rows must stay inside their card',
    )
  } else {
    assert.ok(result.breadth.y >= result.chart.bottom + 10, `Cards must stack below the chart at ${width}px`)
    assert.ok(result.industry.height < 400, `Stacked industry card must retain a natural height at ${width}px`)
    assert.ok(
      result.rows.every((row) => row.height < 60),
      'Stacked rows must not stretch',
    )
  }
}

try {
  const context = await browser.newContext({ viewport: { width: 2560, height: 1331 }, reducedMotion: 'no-preference' })
  // Only read-only replayed responses: no account credentials or production API writes.
  await context.route(base + '/api/**', async (route) => {
    assert.equal(route.request().method(), 'GET')
    const url = new URL(route.request().url())
    const data = url.pathname.endsWith('/market/history')
      ? histories['index-' + url.searchParams.get('symbol')]
      : responseFor(url)
    return route.fulfill({ json: data })
  })
  const page = await context.newPage()
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(base + '/market')
  const chart = page.locator('.two-col .candle-card')
  await chart.locator('.terminal-canvas canvas').first().waitFor()
  await page.waitForTimeout(500)
  for (const width of [2560, 2323, 1920, 1440, 1201, 1200, 1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: width > 1200 ? 1331 : 960 })
    await page.waitForTimeout(150)
    await measure(page, width, 'week')
    await chart.getByRole('button', { name: '日K', exact: true }).click()
    await page.waitForTimeout(100)
    await measure(page, width, 'day')
    await chart.getByRole('button', { name: '周K', exact: true }).click()
    if ([2560, 1440, 390].includes(width)) {
      await page.locator('.two-col').screenshot({ path: path.join(output, `market-${width}.png`) })
    }
  }
  assert.deepEqual(errors, [])
  if (stage === 'before')
    assert.ok(
      checks.some((row) => row.width > 1200 && row.bottomGap > 100),
      'Reproduce the reported bottom gap',
    )
  await writeFile(
    path.join(output, 'results.json'),
    JSON.stringify(
      {
        stage,
        base,
        data: 'Read-only layout fixtures and replayed public history; no production account requests',
        checks,
        errors,
      },
      null,
      2,
    ) + '\n',
  )
  console.log(
    JSON.stringify({
      stage,
      checks: checks.length,
      desktopBottomGaps: checks
        .filter((row) => row.width > 1200)
        .map(({ width, phase, bottomGap }) => ({ width, phase, bottomGap })),
      errors,
    }),
  )
} finally {
  await browser.close()
  await server?.close()
}
