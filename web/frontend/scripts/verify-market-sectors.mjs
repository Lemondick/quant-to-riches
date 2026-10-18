/* global document, innerWidth, window, getComputedStyle */
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const repo = path.resolve(root, '../..')
const captured = JSON.parse(await readFile(path.join(repo, 'output/market-v17/normalized.json'), 'utf8'))
const server = process.env.QUANT_SECTOR_URL
  ? undefined
  : await preview({
      root,
      build: { outDir: path.join(repo, 'output/market-v17/ui/build') },
      preview: { host: '127.0.0.1', port: 0 },
    })
const base = process.env.QUANT_SECTOR_URL || `http://127.0.0.1:${server.httpServer.address().port}`
const output = path.join(repo, 'output/playwright/market-sectors-20261018', process.env.QUANT_SECTOR_STAGE || 'local')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const errors = [],
  checks = []
try {
  for (const width of [320, 390, 768, 1440, 2560]) {
    const context = await browser.newContext({ viewport: { width, height: 1080 } })
    let requests = 0,
      manual = 0
    await context.route(base + '/api/**', (route) => {
      const url = new URL(route.request().url())
      const data = structuredClone(responseFor(url))
      if (url.pathname.endsWith('/market/snapshot')) {
        assert.equal(url.searchParams.get('fullCatalog'), 'true')
        requests++
        if (url.searchParams.get('refresh') === 'true') manual++
        Object.assign(data, {
          sector_rankings: captured.rows,
          sector_catalogs: captured.catalogs,
          market_session: 'closed',
        })
      }
      return route.fulfill({ json: data })
    })
    const page = await context.newPage()
    page.on('pageerror', (error) => errors.push(error.message))
    await page.clock.install()
    await page.goto(base + '/market?tab=sectors')
    const table = page.getByRole('table', { name: '全部板块', exact: true })
    await table.waitFor()
    await page.getByLabel('板块数据源', { exact: true }).selectOption('sina')
    const kind = page.getByRole('group', { name: '板块分类', exact: true })
    for (const [category, name] of [
      ['industry', '行业'],
      ['concept', '概念'],
      ['region', '地域'],
    ]) {
      await kind.getByRole('button', { name, exact: true }).click()
      const rows = captured.rows.filter((item) => item.source === 'sina' && item.category === category)
      await page.locator('.table-record-count b').getByText(String(rows.length), { exact: true }).waitFor()
      assert.equal(await table.locator('tbody tr').count(), Math.min(50, rows.length))
      if (rows.length > 50) {
        await page.getByRole('button', { name: '下一页', exact: true }).click()
        assert.ok((await page.locator('.table-pagination').innerText()).includes('第 2 /'))
        await table.evaluate((element) => {
          const wrap = element.parentElement,
            button = [...element.querySelectorAll('th button')].find((button) => button.textContent.trim() === '涨跌幅')
          const sticky = element.querySelector('th.sticky-name')
          wrap.scrollLeft +=
            button.getBoundingClientRect().left -
            wrap.getBoundingClientRect().left -
            (getComputedStyle(sticky).position === 'sticky' ? sticky.getBoundingClientRect().width : 0) -
            12
          window.scrollTo(0, window.scrollY + button.getBoundingClientRect().top - 200)
        })
        await page.screenshot({ path: path.join(output, `sort-before-${width}.png`) })
        await table.getByRole('button', { name: '涨跌幅', exact: true }).click({ timeout: 3000 })
        const low = [...rows].sort((a, b) => Number(a.change_rate) - Number(b.change_rate))[0]
        assert.ok((await table.locator('tbody tr').first().innerText()).includes(low.code))
        const last = rows.at(-1)
        await page.getByRole('textbox', { name: '搜索全部板块名称或代码', exact: true }).fill(last.code)
        assert.equal(await table.locator('tbody tr').count(), 1)
        assert.ok((await table.locator('tbody').innerText()).includes(last.name))
        await page.getByRole('textbox', { name: '搜索全部板块名称或代码', exact: true }).fill('')
      }
    }
    assert.ok((await page.locator('.sector-source-note').allTextContents()).join(' ').includes('无法确认延迟'))
    await page.getByLabel('板块数据源', { exact: true }).selectOption('eastmoney')
    if (!captured.catalogs.find((item) => item.source === 'eastmoney' && item.category === 'region')?.received) {
      assert.equal(await table.locator('tbody tr').count(), 0)
      assert.ok((await page.locator('.sector-source-note').allTextContents()).join(' ').includes('暂不可用'))
    }
    const emRegion = captured.catalogs.find((item) => item.source === 'eastmoney' && item.category === 'region')
    if (emRegion?.received && emRegion.unquoted === emRegion.received) {
      const values = await table.locator('tbody tr td:nth-child(3)').allTextContents()
      assert.ok(
        values.length > 0 && values.every((value) => value === '—'),
        'Missing quotes must never become zero returns',
      )
    }
    await page.getByLabel('板块数据源', { exact: true }).selectOption('sina')
    const before = requests
    await page.clock.runFor(16000)
    await page.waitForTimeout(200)
    assert.ok(requests > before, 'Visible market must check for replacement data within 15 seconds')
    await page.getByRole('button', { name: '刷新行情', exact: true }).click()
    await page.waitForTimeout(200)
    assert.equal(manual, 1)
    await page.evaluate(() =>
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }),
    )
    const hiddenRequests = requests
    await page.clock.runFor(16000)
    assert.equal(requests, hiddenRequests, 'Hidden market must pause polling')
    await page.evaluate(() => {
      delete document.visibilityState
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await page.waitForTimeout(200)
    assert.ok(requests > hiddenRequests, 'Returning to the market must recheck stale observations')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.screenshot({ path: path.join(output, `sectors-${width}.png`), fullPage: true })
    checks.push({
      width,
      searchAcrossEntireCatalog: true,
      fullSortBeforePaging: true,
      visiblePolling: true,
      manualRefresh: true,
      hiddenPollingPaused: true,
      visibleReturnRechecks: true,
      unmountDisposesPolling: true,
      missingQuotesNeverBecomeZero: true,
      overflow: false,
    })
    await page.getByRole('link', { name: '资产总览', exact: true }).click()
    await page.waitForTimeout(200)
    const unmounted = requests
    await page.clock.runFor(16000)
    await page.waitForTimeout(200)
    assert.equal(requests, unmounted, 'Leaving the market must dispose its polling timer')
    await context.close()
  }
  assert.deepEqual(errors, [])
  await writeFile(
    path.join(output, 'evidence.json'),
    JSON.stringify({ replayOfActualPublicObservations: true, checks, errors }, null, 2),
  )
  console.log(JSON.stringify({ passed: checks.length, errors }))
} finally {
  await browser.close()
  if (server) await new Promise((resolve) => server.httpServer.close(resolve))
}
