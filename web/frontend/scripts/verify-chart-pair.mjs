/* global window, document, requestAnimationFrame, getComputedStyle */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const repo = path.resolve(root, '../..')
const stage = process.env.QUANT_CHART_STAGE || 'after'
const build = process.env.QUANT_CHART_BUILD || path.join(repo, 'output/ui-chart-pair-v13/build')
const output = path.join(repo, 'output/playwright/chart-pair-20261017', stage)
await mkdir(output, { recursive: true })
const server = await preview({ root, build: { outDir: build }, preview: { host: '127.0.0.1', port: 0 } })
const base = `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ headless: true })
const errors = [],
  results = []
try {
  for (const width of [320, 390, 1440, 2323])
    for (const empty of [true, false]) {
      const context = await browser.newContext({ viewport: { width, height: 1080 }, reducedMotion: 'no-preference' })
      await context.route(base + '/api/**', async (route) => {
        assert.equal(route.request().method(), 'GET')
        const url = new URL(route.request().url())
        const data = structuredClone(responseFor(url))
        if (url.pathname.endsWith('/portfolio/history')) {
          await new Promise((resolve) => setTimeout(resolve, 180))
          if (empty) data.points = []
        }
        if (empty && url.pathname.endsWith('/dashboard/summary')) {
          data.portfolio.positions = []
          data.portfolio.cash = '0'
          data.portfolio.total_value = '0'
        }
        return route.fulfill({ json: data })
      })
      await context.route(base + '/assets/**', async (route) => {
        // Hold the heavy plotting code, while account and allocation data are ready.
        if (/\/(ChartCard|lineChart)-.*\.js$/.test(new URL(route.request().url()).pathname))
          await new Promise((resolve) => setTimeout(resolve, 650))
        return route.continue()
      })
      await context.addInitScript(() => {
        window.__pairFrames = []
        function sample() {
          const grid = document.querySelector('.dashboard-grid')
          if (grid) window.__pairStarted = true
          if (window.__pairStarted) {
            const measure = (selector) => {
              const node = grid?.querySelector(selector)
              if (!node) return null
              const rect = node.getBoundingClientRect()
              let visible = rect.width > 0 && rect.height > 0
              for (let parent = node; parent; parent = parent.parentElement)
                if (Number(getComputedStyle(parent).opacity) < 0.98) visible = false
              return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, visible }
            }
            window.__pairFrames.push({ chart: measure('.chart-panel'), donut: measure('.donut-panel') })
          }
          requestAnimationFrame(sample)
        }
        requestAnimationFrame(sample)
      })
      const page = await context.newPage()
      page.on('pageerror', (error) => errors.push(error.message))
      async function capture(label, action, waitForChart = true) {
        await page.evaluate(() => {
          window.__pairFrames = []
          window.__pairStarted = false
        })
        await action()
        await page.locator('.dashboard-grid .chart-panel').waitFor()
        if (waitForChart) await page.locator('.chart-panel canvas').waitFor()
        await page.waitForTimeout(250)
        const frames = await page.evaluate(() => window.__pairFrames)
        assert.ok(frames.length > 2)
        const split = frames.filter((frame) => !!frame.chart?.visible !== !!frame.donut?.visible)
        const complete = frames.filter((frame) => frame.chart && frame.donut)
        const disappeared = frames.filter((frame) => !frame.chart && !frame.donut)
        const initial = complete[0]
        const movement = Math.max(
          0,
          ...complete.flatMap((frame) =>
            ['chart', 'donut'].flatMap((card) =>
              ['x', 'y', 'width', 'height'].map((key) => Math.abs(frame[card][key] - initial[card][key])),
            ),
          ),
        )
        if (stage !== 'before') {
          assert.equal(split.length, 0, `${label} ${width}px: panels appeared on separate frames`)
          assert.equal(disappeared.length, 0, `${label} ${width}px: both panels disappeared`)
          assert.ok(movement < 1, `${label} ${width}px empty=${empty}: card moved ${movement}px`)
        }
        results.push({
          width,
          empty,
          label,
          frames: frames.length,
          split_frames: split.length,
          disappeared_frames: disappeared.length,
          max_movement_px: movement,
        })
      }
      await page.goto(base + '/settings')
      await page.getByRole('heading', { name: '账号与设置', exact: true }).waitFor()
      await capture('first visit', () => page.locator('.sidebar a[href="/"]').click())
      await capture('refresh data', () => page.getByRole('button', { name: '刷新数据', exact: true }).click())
      await page.locator('.sidebar a[href="/portfolio"]').click()
      await page.getByRole('heading', { name: '组合分析', exact: true }).waitFor()
      await capture('return home', () => page.locator('.sidebar a[href="/"]').click())
      // Fresh document and cold HTTP cache expose first-render async placeholders.
      await context.clearCookies()
      await capture('reload home', () => page.reload({ waitUntil: 'domcontentloaded' }))
      if (width === 2323 && empty)
        await page.screenshot({ path: path.join(output, 'home-empty-wide.png'), fullPage: true })
      await context.close()
    }
  assert.deepEqual(errors, [])
  await writeFile(
    path.join(output, 'evidence.json'),
    JSON.stringify(
      {
        stage,
        build,
        scope: 'Read-only local account fixtures; normal motion; chart module delayed 650ms and history delayed 180ms',
        results,
        errors,
      },
      null,
      2,
    ),
  )
  console.log(
    JSON.stringify({
      stage,
      checks: results.length,
      split_frames: results.reduce((sum, result) => sum + result.split_frames, 0),
      max_movement_px: Math.max(...results.map((result) => result.max_movement_px)),
      errors,
    }),
  )
} finally {
  await browser.close()
  await server.httpServer.close()
}
