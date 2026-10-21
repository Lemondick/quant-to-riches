/* global document, innerWidth */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { responseFor } from './ui-layout-fixtures.mjs'
import { catalogResponse, catalogDimensions } from './fund-catalog-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const build = path.resolve(process.env.QUANT_WEB_OUTPUT_DIR || path.join(root, '../../data/runtime/web'), 'build')
const output = path.resolve(root, '../../output/playwright/fund-catalog')
await mkdir(output, { recursive: true })
const server = await preview({ root, build: { outDir: build }, preview: { host: '127.0.0.1', port: 0 } })
const base = `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ headless: true })
const errors = [],
  checks = [],
  mutations = [],
  requests = []
let mode = 'normal',
  watchlist = [],
  releaseOld,
  markOld
const oldRequested = new Promise((resolve) => {
  markOld = resolve
})
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' })
  page.setDefaultTimeout(10000)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route(base + '/api/**', async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      endpoint = url.pathname.replace('/api/v1', '')
    requests.push(endpoint)
    if (req.method() !== 'GET') {
      assert.equal(req.method(), 'PUT')
      assert.equal(endpoint, '/preferences/watchlist')
      assert.equal(req.headers()['x-csrf-token'], 'layout-only-not-valid-on-any-server')
      assert.equal(req.headers()['x-session-scope'], 'layout-only')
      mutations.push(req.postDataJSON())
      watchlist = req.postDataJSON().items
      return route.fulfill({ json: {} })
    }
    if (endpoint === '/funds/catalog') {
      if (mode === 'unavailable')
        return route.fulfill({ status: 503, json: { error_code: 'public_snapshot_unavailable' } })
      if (mode === 'late' && url.searchParams.get('q') === 'old') {
        markOld()
        await new Promise((resolve) => {
          releaseOld = resolve
        })
      }
      return route.fulfill({ json: catalogResponse(url, mode) })
    }
    if (endpoint === '/funds/categories') return route.fulfill({ json: { dimensions: catalogDimensions } })
    if (/^\/funds\/[0-9]{6}\/detail$/.test(endpoint))
      return route.fulfill({
        json: {
          symbol: endpoint.split('/')[2],
          name: '明确验收基金详情',
          fund_type: '股票型',
          company: '验收资料',
          observed_at: '2026-10-20T08:00:00Z',
          stale: false,
          nav_history: [{ nav_date: '2026-10-19', unit_nav: '1.2300' }],
        },
      })
    if (mode === 'market-unavailable' && endpoint === '/market/snapshot')
      return route.fulfill({ status: 503, json: {} })
    const data = structuredClone(responseFor(url))
    if (endpoint === '/dashboard/summary') data.preferences.watchlist = watchlist
    return route.fulfill({ json: data })
  })
  async function open() {
    await page.goto(base + '/market?tab=funds')
    await page.getByRole('button', { name: '分类目录', exact: true }).click()
  }
  const table = page.getByRole('table', { name: '基金分类结果', exact: true })
  await open()
  await table.locator('tbody tr').first().waitFor()
  assert.equal(await table.locator('tbody tr').count(), 50)
  assert.match(await table.locator('tbody tr').nth(0).innerText(), /货币基金口径待核验/)
  assert.match(await table.locator('tbody tr').nth(1).innerText(), /1\.2300[\s\S]*0\.00%/)
  assert.match(await table.locator('tbody tr').nth(2).innerText(), /资产待核验/)
  checks.push('unknown, money metric and actual zero remain distinct')
  for (const width of [320, 390, 1024, 1440, 1920, 2560]) {
    await page.setViewportSize({ width, height: 960 })
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      `page overflow at ${width}`,
    )
    await page.screenshot({ path: path.join(output, `catalog-${width}.png`), fullPage: false })
    checks.push(`catalog responsive ${width}`)
  }
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await page.waitForFunction(
    () => document.querySelector('table[aria-label="基金分类结果"] tbody tr td:nth-child(2)')?.textContent === '100050',
  )
  checks.push('server pagination retrieves next 50, not an in-browser full directory')
  await page.getByRole('button', { name: '清空筛选', exact: true }).click()
  const asset = page
    .locator('.catalog-filters details')
    .filter({ has: page.locator('summary').filter({ hasText: /^资产类别/ }) })
  await asset.locator('summary').click()
  await asset.getByRole('checkbox', { name: /股票型/ }).check()
  const strategy = page
    .locator('.catalog-filters details')
    .filter({ has: page.locator('summary').filter({ hasText: /^策略标签/ }) })
  await strategy.locator('summary').click()
  await strategy.getByRole('checkbox', { name: /指数策略/ }).check()
  await page.getByRole('textbox', { name: '代码或名称' }).fill('100003')
  await page.getByRole('button', { name: '搜索目录', exact: true }).click()
  await page.waitForFunction(
    () => document.querySelector('table[aria-label="基金分类结果"] tbody tr td:nth-child(2)')?.textContent === '100003',
  )
  assert.equal(await table.locator('tbody tr').count(), 1)
  checks.push('search composes with simultaneous asset and strategy filters')
  await table.getByRole('button', { name: '关注：明确验收基金3', exact: true }).click()
  await table.getByRole('button', { name: '取消关注：明确验收基金3', exact: true }).waitFor()
  assert.deepEqual(mutations[0].items, [{ symbol: '100003', name: '明确验收基金3', asset_type: 'fund' }])
  await table.getByRole('button', { name: '明确验收基金3', exact: true }).click()
  await page.getByRole('region', { name: '基金公开资料', exact: true }).waitFor()
  checks.push('detail and account follow reuse existing authenticated endpoints')
  await page.getByRole('button', { name: '日涨幅榜', exact: true }).click()
  await page.getByRole('table', { name: '基金排行', exact: true }).waitFor()
  checks.push('original ranking and controls preserved')
  for (const state of ['stale', 'empty', 'market-unavailable', 'unavailable']) {
    mode = state
    await open()
    if (state === 'unavailable')
      await page
        .getByRole('alert')
        .filter({ hasText: /数据暂不可用/ })
        .last()
        .waitFor()
    else if (state === 'empty') await page.getByText('没有符合条件的基金', { exact: true }).waitFor()
    else {
      await table.locator('tbody tr').first().waitFor()
      if (state === 'stale') await page.getByText(/目录已过期/).waitFor()
    }
    checks.push(state + ' keeps an honest independent catalog state')
  }
  mode = 'late'
  await open()
  await table.locator('tbody tr').first().waitFor()
  await page.getByRole('textbox', { name: '代码或名称' }).fill('old')
  await page.getByRole('button', { name: '搜索目录', exact: true }).click()
  await oldRequested
  await page.getByRole('textbox', { name: '代码或名称' }).fill('100005')
  await page.getByRole('button', { name: '搜索目录', exact: true }).click()
  await page.waitForFunction(
    () => document.querySelector('table[aria-label="基金分类结果"] tbody tr td:nth-child(2)')?.textContent === '100005',
  )
  releaseOld()
  await page.waitForResponse((res) => new URL(res.url()).searchParams.get('q') === 'old')
  assert.equal(await table.locator('tbody tr').count(), 1)
  assert.match(await table.innerText(), /100005/)
  checks.push('late result for another search cannot replace selected results')
  assert.equal(errors.length, 0, errors.join('\n'))
  assert.equal(mutations.length, 1)
  await writeFile(
    path.join(output, 'acceptance.json'),
    JSON.stringify({ fixture_only: true, checks, errors, mutation_count: mutations.length, requests }, null, 2),
  )
  console.log(JSON.stringify({ passed: checks.length, checks, errors, output }, null, 2))
} finally {
  if (releaseOld) releaseOld()
  await browser.close()
  await server.close()
}
