/* global document, innerWidth, getComputedStyle */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { indexRegistry, responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const build = path.resolve(process.env.QUANT_WEB_OUTPUT_DIR || path.join(root, '../../data/runtime/web'), 'build')
const output = path.resolve(root, '../../output/playwright/index-strip')
await mkdir(output, { recursive: true })
const server = await preview({ root, build: { outDir: build }, preview: { host: '127.0.0.1', port: 0 } })
const base = `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ headless: true })
const errors = [],
  checks = []
let mode = 'normal'
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' })
  page.setDefaultTimeout(10000)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route(base + '/api/**', async (route) => {
    assert.equal(route.request().method(), 'GET')
    const url = new URL(route.request().url()),
      data = structuredClone(responseFor(url))
    if (mode === 'mixed' && url.pathname.endsWith('/market/snapshot')) {
      data.indices.splice(4, 1)
      data.index_statuses[4].quality_status = 'unavailable'
      data.indices[0].cache_fallback = true
      data.index_statuses[1].quality_status = 'unknown_time'
      data.index_statuses[2].quality_status = 'invalid'
    }
    return route.fulfill({ json: data })
  })
  const snapshotReady = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/market/snapshot'),
  )
  await page.goto(base + '/market')
  await snapshotReady
  console.log('Connected snapshot received; legacy grid cards:', await page.locator('.stats-grid .stat-card').count())
  await page.locator('[data-index-id]').first().waitFor()
  assert.equal(await page.locator('[data-index-id]').count(), 16)
  assert.deepEqual(
    await page
      .locator('[data-index-id]')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-index-id'))),
    indexRegistry.map((row) => row.id),
  )
  assert.equal(await page.locator('.sparkline').count(), 0)
  assert.equal(await page.getByLabel('选择指数', { exact: true }).locator('option').count(), 16)
  for (const [i, tone] of ['down', 'neutral', 'up'].entries())
    assert.ok((await page.locator('[data-index-id]').nth(i).getAttribute('class')).includes(tone))
  assert.match(await page.locator('[data-index-id]').last().innerText(), /暂定/)
  for (const width of [1440, 1920, 2560, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 960 })
    const layout = await page.locator('.index-strip').evaluate((track) => ({
      documentWidth: document.documentElement.scrollWidth,
      viewport: innerWidth,
      track: track.clientWidth,
      card: track.firstElementChild.getBoundingClientRect().width,
      gap: parseFloat(getComputedStyle(track).columnGap),
      snap: getComputedStyle(track).scrollSnapType,
    }))
    assert.ok(layout.documentWidth <= width + 1, JSON.stringify(layout))
    if (width >= 1024) {
      const visible = (layout.track + layout.gap) / (layout.card + layout.gap)
      assert.ok(visible >= 3.99 && visible <= 6.01, JSON.stringify(layout))
    }
    if (width < 640) assert.match(layout.snap, /x/)
    await page.locator('.index-strip').focus()
    await page.keyboard.press('End')
    await page.waitForTimeout(100)
    assert.ok(
      await page
        .locator('.index-strip')
        .evaluate((track) => track.scrollLeft + track.clientWidth >= track.scrollWidth - 2),
    )
    await page.keyboard.press('Home')
    await page.screenshot({ path: path.join(output, `${width}.png`), fullPage: true })
    checks.push({ width, ...layout })
  }
  await page.getByLabel('选择指数', { exact: true }).selectOption('jp.nikkei225')
  await page.getByText('历史行情尚未接入', { exact: true }).waitFor()
  assert.equal(await page.locator('.candle-card canvas').count(), 0)
  mode = 'mixed'
  await page.reload()
  await page.locator('[data-index-id]').first().waitFor()
  assert.equal(await page.locator('[data-index-id]').count(), 16)
  assert.match(await page.locator('[data-index-id]').nth(0).innerText(), /最近有效快照/)
  assert.match(await page.locator('[data-index-id]').nth(1).innerText(), /时间待核验/)
  assert.match(await page.locator('[data-index-id]').nth(2).innerText(), /数据校验未通过/)
  assert.match(await page.locator('[data-index-id]').nth(4).innerText(), /暂不可用/)
  assert.equal(await page.locator('[data-index-id]').nth(4).locator('.stat-value').innerText(), '—')
  assert.deepEqual(errors, [])
  await writeFile(
    path.join(output, 'summary.json'),
    JSON.stringify(
      { fixture: 'explicit-local-layout-fixture, no production or account writes', checks, errors },
      null,
      2,
    ),
  )
  console.log(`PASS sixteen index cards, colors, quality, registry history and ${checks.length} responsive widths`)
} finally {
  await browser.close()
  await server.close()
}
