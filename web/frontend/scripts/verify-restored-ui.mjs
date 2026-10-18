/* global innerWidth, document, getComputedStyle */
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { createServer, preview } from 'vite'
import { responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const repo = path.resolve(root, '../..')
const reference = path.resolve(repo, '../VUE')
const output = path.join(repo, 'output/playwright/ui-restored-20261017')
await mkdir(output, { recursive: true })
const referenceServer = await createServer({
  root: reference,
  cacheDir: path.join(output, 'reference-cache'),
  server: { host: '127.0.0.1', port: 0 },
})
await referenceServer.listen()
const liveServer = await preview({
  root,
  build: { outDir: path.join(repo, 'output/ui-restore-20261017/connected/build') },
  preview: { host: '127.0.0.1', port: 0 },
})
const bases = {
  reference: `http://127.0.0.1:${referenceServer.httpServer.address().port}`,
  restored: `http://127.0.0.1:${liveServer.httpServer.address().port}`,
}
const routes = [
  ['/', '资产总览'],
  ['/portfolio', '组合分析'],
  ['/ledger', '交易账本'],
  ['/watchlist', '我的自选'],
  ['/market', '市场行情'],
  ['/ai-stock', 'AI选股'],
  ['/fund-review', '基金复核'],
  ['/quant', '量化策略'],
  ['/news', '市场资讯'],
  ['/admin', '管理控制台'],
  ['/settings', '账号与设置'],
]
const errors = [],
  checks = [],
  geometries = {}
let browser

try {
  browser = await chromium.launch({ headless: true })
  for (const [kind, base] of Object.entries(bases)) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' })
    await context.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) => route.abort())
    if (kind === 'restored')
      await context.route(base + '/api/**', (route) => {
        assert.equal(route.request().method(), 'GET', 'Visual verification never writes business data')
        return route.fulfill({ json: responseFor(new URL(route.request().url())) })
      })
    const page = await context.newPage()
    page.on('pageerror', (error) => errors.push(kind + ': ' + error.message))
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 960 })
      for (const [route, title] of routes) {
        await page.goto(base + route)
        await page.getByRole('heading', { level: 1, name: title, exact: true }).waitFor()
        if (kind === 'restored') await page.getByText('登录账户', { exact: true }).waitFor({ state: 'hidden' })
        if (route === '/') await page.locator('.dashboard-grid .echart').waitFor()
        if (route === '/' || route === '/market' || route === '/admin')
          await page.locator('.stats-grid .stat-card').first().waitFor()
        const metrics = await page.evaluate(() => {
          const selectors = [
            '.sidebar',
            '.topbar',
            '.page-shell',
            '.page-header',
            '.stats-grid',
            '.dashboard-grid',
            '.setting-grid',
          ]
          return {
            width: innerWidth,
            scroll: document.documentElement.scrollWidth,
            elements: Object.fromEntries(
              selectors.map((selector) => {
                const node = document.querySelector(selector)
                if (!node) return [selector, null]
                const rect = node.getBoundingClientRect(),
                  style = getComputedStyle(node)
                return [
                  selector,
                  {
                    x: rect.x,
                    width: rect.width,
                    height: rect.height,
                    fontSize: style.fontSize,
                    background: style.backgroundColor,
                    padding: style.padding,
                  },
                ]
              }),
            ),
          }
        })
        assert.ok(metrics.scroll <= metrics.width + 1, kind + ' ' + route + ' overflows at ' + width)
        geometries[kind + ':' + width + ':' + route] = metrics
        await page.screenshot({
          path: path.join(output, kind + '-' + width + '-' + (route.slice(1) || 'home') + '.png'),
          fullPage: true,
        })
        checks.push(kind + ' ' + width + ' ' + title)
      }
    }
    if (kind === 'restored') {
      await page.setViewportSize({ width: 1440, height: 960 })
      await page.goto(base + '/market')
      await page.getByRole('button', { name: '基金榜', exact: true }).click()
      await page.getByRole('table', { name: '基金排行', exact: true }).getByText('布局验收基金').waitFor()
      await page.screenshot({ path: path.join(output, 'restored-1440-funds.png'), fullPage: true })
      await page.goto(base + '/settings?section=models')
      await page.getByRole('region', { name: '账户模型连接' }).waitFor()
      await page.screenshot({ path: path.join(output, 'restored-1440-models.png'), fullPage: true })
      await page.goto(base + '/ledger')
      await page.getByRole('button', { name: '新增记录', exact: true }).click()
      await page.getByRole('dialog', { name: '新增交易记录', exact: true }).waitFor()
      await page.screenshot({ path: path.join(output, 'restored-1440-ledger-dialog.png'), fullPage: true })
      checks.push('Restored market tabs, model settings, and ledger dialog open')
    }
    await context.close()
  }
  for (const width of [1440, 390]) {
    for (const route of ['/', '/market', '/settings']) {
      const left = geometries['reference:' + width + ':' + route].elements,
        right = geometries['restored:' + width + ':' + route].elements
      for (const selector of ['.sidebar', '.topbar', '.page-shell', '.page-header']) {
        for (const key of ['x', 'width', 'fontSize', 'background', 'padding'])
          assert.equal(right[selector]?.[key], left[selector]?.[key], width + ' ' + route + ' ' + selector + ' ' + key)
      }
    }
  }
  assert.equal(errors.length, 0, errors.join('\n'))
  const originalCss = await readFile(path.join(reference, 'src/styles/theme.css'), 'utf8')
  const actualCss = await readFile(path.join(root, 'src/styles/theme.css'), 'utf8')
  assert.equal(actualCss.trim(), originalCss.replace(/^@import[^\n]+\n/, '').trim())
  await writeFile(
    path.join(output, 'evidence.json'),
    JSON.stringify(
      {
        reference,
        fixture_scope: 'Explicit local visual fixtures only; no production requests or writes',
        checks,
        errors,
        geometries,
        original_theme_preserved_except_remote_font_import: true,
      },
      null,
      2,
    ),
  )
  console.log('PASS ' + checks.length + ' visual checks; original shell dimensions and theme match')
} finally {
  await browser?.close()
  await liveServer.httpServer.close()
  await referenceServer.close()
}
