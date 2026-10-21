/* global document, innerWidth */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'
import { responseFor } from './ui-layout-fixtures.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const build = path.resolve(process.env.QUANT_WEB_OUTPUT_DIR || path.join(root, '../../data/runtime/web'), 'build')
const output = path.resolve(root, '../../output/playwright/sector-quality')
await mkdir(output, { recursive: true })
const server = await preview({ root, build: { outDir: build }, preview: { host: '127.0.0.1', port: 0 } })
const base = `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ headless: true })
const errors = [],
  checks = []
let mode = 'normal'
const rows = ['eastmoney', 'sina'].flatMap((source) =>
  ['industry', 'concept', 'region'].map((category, i) => ({
    source,
    category,
    code: 'BK0001',
    name: '明确验收同名板块',
    change_rate: String(source === 'eastmoney' ? i : -i - 1),
    turnover: '100000000',
    source_time: source === 'eastmoney' ? '2026-10-20T15:00:00+08:00' : null,
    observed_at: source === 'eastmoney' ? '2026-10-20T15:00:00+08:00' : null,
    fetched_at: '2026-10-21T08:00:00+08:00',
    quality_status: source === 'eastmoney' ? 'valid' : 'unknown_time',
  })),
)
rows.push(
  ...[
    { code: 'BK0002', name: '明确验收缺报价', change_rate: null, quality_status: 'unavailable', source_time: null },
    {
      code: 'BK0003',
      name: '明确验收无效报价',
      change_rate: '99',
      quality_status: 'invalid',
      source_time: '2099-01-01T15:00:00+08:00',
    },
    {
      code: 'BK0004',
      name: '明确验收旧报价',
      change_rate: '1.25',
      quality_status: 'stale',
      source_time: '2026-10-16T15:00:00+08:00',
      is_stale: true,
    },
  ].map((row) => ({ ...row, source: 'eastmoney', category: 'industry' })),
)
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' })
  page.setDefaultTimeout(10000)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route(base + '/api/**', async (route) => {
    assert.equal(route.request().method(), 'GET')
    const url = new URL(route.request().url()),
      data = structuredClone(responseFor(url))
    if (url.pathname.endsWith('/market/snapshot')) {
      data.sector_rankings = structuredClone(
        rows.filter((row) => mode !== 'one-source-failed' || row.source !== 'eastmoney'),
      )
      data.sector_catalogs = ['eastmoney', 'sina'].flatMap((source) =>
        ['industry', 'concept', 'region'].map((category) => {
          const count = data.sector_rankings.filter((row) => row.source === source && row.category === category).length
          return {
            source,
            category,
            received: count,
            total: null,
            complete: null,
            unquoted: count === 4 ? 1 : 0,
            observed_at: source === 'eastmoney' ? '2026-10-20T15:00:00+08:00' : null,
            fetched_at: '2026-10-21T08:00:00+08:00',
            source_time_known: source === 'eastmoney' && category !== 'industry',
            quote_total: source === 'eastmoney' ? count - (count === 4 ? 1 : 0) : null,
            quote_payload_complete: source === 'eastmoney',
            catalog_stale: source === 'eastmoney' && category === 'industry',
            stale: source === 'eastmoney' && (category === 'industry' || !count),
            http_ok: count > 0,
            parse_ok: count > 0,
          }
        }),
      )
    }
    return route.fulfill({ json: data })
  })
  await page.goto(base + '/market?tab=sectors')
  await page.getByLabel('板块数据源', { exact: true }).selectOption('eastmoney')
  const table = page.getByRole('table', { name: '全部板块', exact: true })
  await table.locator('tbody tr').first().waitFor()
  const zero = table.locator('tbody tr').filter({ hasText: '明确验收同名板块' })
  assert.match(await zero.innerText(), /0\.00%/)
  assert.match(await table.locator('tbody tr').filter({ hasText: '明确验收缺报价' }).innerText(), /暂无报价/)
  const invalid = table.locator('tbody tr').filter({ hasText: '明确验收无效报价' })
  assert.match(await invalid.innerText(), /校验失败/)
  assert.doesNotMatch(await invalid.innerText(), /99\.00%|2099-/)
  assert.match(
    await table.locator('tbody tr').filter({ hasText: '明确验收旧报价' }).innerText(),
    /2026-10-16[\s\S]*已过期/,
  )
  await page.getByText(/目录刷新失败，保留旧目录/).waitFor()
  await page.getByText(/部分或全部报价时间未知/).waitFor()
  checks.push('zero, missing, invalid, stale and combined directory/time status')
  for (const width of [320, 390, 1024, 1440, 1920, 2560]) {
    await page.setViewportSize({ width, height: 960 })
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      `sector overflow ${width}`,
    )
    if ([390, 1440].includes(width))
      await page.screenshot({ path: path.join(output, `sectors-${width}.png`), fullPage: false })
    checks.push('sector layout ' + width)
  }
  await page.getByLabel('板块数据源', { exact: true }).selectOption('sina')
  assert.equal(await table.locator('tbody tr').count(), 1)
  assert.match(await table.innerText(), /-1\.00%[\s\S]*来源时间未知/)
  await page.getByText(/新浪分类；涨跌幅为源提供的板块平均涨跌/).waitFor()
  for (const [category, expected] of [
    ['概念', '-2.00%'],
    ['地域', '-3.00%'],
  ]) {
    await page.getByRole('button', { name: category, exact: true }).click()
    assert.equal(await table.locator('tbody tr').count(), 1)
    assert.ok((await table.innerText()).includes(expected))
  }
  checks.push('identical names/codes do not merge vendors or industry/concept/region')
  mode = 'one-source-failed'
  await page.reload()
  await page.getByLabel('板块数据源', { exact: true }).selectOption('sina')
  await table.locator('tbody tr').first().waitFor()
  assert.equal(await table.locator('tbody tr').count(), 1)
  checks.push('one supplier failure preserves other supplier, never borrows an index')
  await page.getByRole('button', { name: '指数', exact: true }).click()
  await page.locator('[data-index-id]').first().waitFor()
  assert.equal(await page.locator('[data-index-id]').count(), 15)
  checks.push('fifteen index registry remains independent of sector filters')
  assert.deepEqual(errors, [])
  await writeFile(path.join(output, 'acceptance.json'), JSON.stringify({ fixture_only: true, checks, errors }, null, 2))
  console.log(JSON.stringify({ passed: checks.length, checks, errors, output }, null, 2))
} finally {
  await browser.close()
  await server.close()
}
