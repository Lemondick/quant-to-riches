/* global document, window */
import assert from 'node:assert/strict'
import { writeFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { preview } from 'vite'
import { chromium } from 'playwright'
import { responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const repo = path.resolve(root, '../..')
const output = path.join(repo, 'output/data-quality-v18/ui-verification')
await mkdir(output, { recursive: true })
const servers = await Promise.all(
  ['output/market-v17/ui/build', 'output/data-quality-v18/ui/build'].map((outDir) =>
    preview({ root, build: { outDir: path.join(repo, outDir) }, preview: { host: '127.0.0.1', port: 0 } }),
  ),
)
const browser = await chromium.launch({ headless: true })
const checks = [],
  errors = []
try {
  for (const width of [390, 1440]) {
    for (const pagePath of ['/market', '/market?tab=funds', '/market?tab=sectors', '/', '/fund-review', '/watchlist']) {
      const rectangles = []
      for (const [stage, server] of servers.entries()) {
        const base = `http://127.0.0.1:${server.httpServer.address().port}`
        const context = await browser.newContext({ viewport: { width, height: 1080 }, reducedMotion: 'reduce' })
        await context.route(base + '/api/**', (route) => {
          const url = new URL(route.request().url())
          let data = /\/funds\/\d{6}\/detail$/.test(url.pathname)
            ? {
                symbol: '510300',
                name: '布局验收基金',
                fund_type: '指数基金',
                company: '布局验收',
                observed_at: '2026-10-16T15:00:00+08:00',
                stale: false,
                nav_history: ['2026-10-14', '2026-10-15', '2026-10-16'].map((nav_date, i) => ({
                  nav_date,
                  unit_nav: String(1 + i / 100),
                })),
              }
            : structuredClone(responseFor(url))
          const visit = (value) => {
            if (!value || typeof value !== 'object') return
            if (value.symbol && value.observed_at)
              Object.assign(value, { updated_at: value.observed_at, quality_status: 'valid', is_stale: false })
            if (value.estimate_nav) value.valuation_type = 'intraday_estimate'
            for (const child of Object.values(value)) visit(child)
          }
          visit(data)
          if (url.pathname.endsWith('/market/history')) data.updated_at = '2026-10-16'
          if (url.pathname.endsWith('/detail')) Object.assign(data, { updated_at: null, nav_updated_at: '2026-10-16' })
          return route.fulfill({ json: data })
        })
        const page = await context.newPage()
        page.setDefaultTimeout(10000)
        page.on('pageerror', (error) => errors.push(error.message))
        await page.clock.install({ time: new Date('2026-10-18T10:00:00+08:00') })
        await page.goto(base + pagePath)
        await page.locator('main .panel:visible').first().waitFor()
        if (pagePath === '/fund-review') {
          await page.locator('table .text-action').first().click()
          await page.locator('.detail-title').waitFor()
        }
        await page.waitForTimeout(600)
        await page.addStyleTag({
          content:
            '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}',
        })
        await page.evaluate(() => document.fonts.ready)
        if (stage === 1) {
          assert.ok(await page.locator('[data-update-time]').count(), 'module timestamp missing')
          if (pagePath.includes('sectors') || pagePath === '/fund-review')
            assert.ok(
              (await page.locator('[data-update-time]').allTextContents()).some((value) => value.includes('未知')),
            )
          await page.screenshot({
            path: path.join(output, `${width}-${pagePath.replace(/\W/g, '_')}-timestamps.png`),
            fullPage: true,
          })
        }
        // The changed legacy heading itself was an old collection-time string.
        // Mask all timestamp text in both releases before checking pixels/geometry.
        await page.evaluate(() => {
          document.querySelectorAll('[data-update-time]').forEach((element) => element.remove())
          document.querySelectorAll('.panel-head p').forEach((element) => {
            if (element.textContent.startsWith('观测时间')) element.textContent = ''
          })
        })
        await page.mouse.move(0, 0)
        await page.waitForTimeout(300)
        const rects = await page.locator('main .panel, main .stat-card').evaluateAll((elements) =>
          elements.map((element) => {
            const r = element.getBoundingClientRect()
            return [r.x, r.y, r.width, r.height].map((value) => Math.round(value * 100) / 100)
          }),
        )
        rectangles.push(rects)
        await page.screenshot({
          path: path.join(output, `${width}-${pagePath.replace(/\W/g, '_')}-${stage ? 'after' : 'before'}.png`),
          fullPage: true,
        })
        assert.ok(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
          'horizontal page overflow',
        )
        await context.close()
      }
      assert.deepEqual(rectangles[0], rectangles[1], `layout changed outside timestamp text: ${width} ${pagePath}`)
      checks.push({ width, pagePath, geometry_equal: true })
      console.log(JSON.stringify(checks.at(-1)))
    }
  }
  assert.deepEqual(errors, [])
  await writeFile(path.join(output, 'checks.json'), JSON.stringify({ checks, errors }, null, 2))
  console.log(JSON.stringify({ checks: checks.length, errors }))
} finally {
  await browser.close()
  await Promise.all(servers.map((server) => new Promise((resolve) => server.httpServer.close(resolve))))
}
