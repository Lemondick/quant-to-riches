import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { preview } from 'vite'

const root = fileURLToPath(new URL('../', import.meta.url))
const output = path.resolve(
  process.env.QUANT_BROWSER_OUTPUT || path.join(root, '../../output/playwright/vue-exact-20261017'),
)
const build = path.resolve(process.env.QUANT_WEB_OUTPUT_DIR || path.join(root, '../../data/runtime/web'), 'build')
await mkdir(output, { recursive: true })
const server = await preview({ root, build: { outDir: build }, preview: { host: '127.0.0.1', port: 0 } })
let browser
let page
const results = []
const errors = []
const externalRequests = []
const fontWarnings = []
const isTemplateFont = (url) => /^https:\/\/(fonts\.googleapis\.com|fonts\.gstatic\.com)\//.test(url)
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
const base = `http://127.0.0.1:${server.httpServer.address().port}`
const passed = (name) => {
  results.push(name)
  console.log(`PASS ${name}`)
}

async function visit(route, title) {
  const response = await page.goto(base + route)
  assert.equal(response.status(), 200)
  await page.getByRole('heading', { level: 1, name: title, exact: true }).waitFor()
}

async function layout(label) {
  const size = await page.evaluate(() => ({
    width: globalThis.innerWidth,
    scroll: globalThis.document.documentElement.scrollWidth,
  }))
  assert.ok(size.scroll <= size.width + 1, `${label}: document overflows ${size.scroll - size.width}px`)
  passed(label)
}

try {
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' })
  context.on('request', (request) => {
    if (!request.url().startsWith(base + '/') && !/^(data:|blob:)/.test(request.url()))
      externalRequests.push(request.url())
  })
  context.on('requestfailed', (request) => {
    if (isTemplateFont(request.url())) fontWarnings.push({ url: request.url(), error: request.failure()?.errorText })
  })
  page = await context.newPage()
  page.setDefaultTimeout(10000)
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error' && !isTemplateFont(message.location().url)) errors.push(message.text())
  })
  for (const width of [1440, 1920, 1024, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 960 })
    for (const [route, title] of routes) {
      await visit(route, title)
      await layout(`${width}px ${title}: direct navigation and layout`)
      if ((width === 1440 && ['/', '/market', '/admin'].includes(route)) || (width === 390 && route === '/')) {
        if (route === '/' || route === '/market') await page.locator('canvas').first().waitFor()
        await page.screenshot({ path: path.join(output, `${width}-${route.slice(1) || 'home'}.png`), fullPage: true })
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 960 })

  await visit('/market?tab=funds', '市场行情')
  assert.equal(new URL(page.url()).pathname, '/market')
  assert.equal(new URL(page.url()).searchParams.get('tab'), 'funds')
  await page.getByRole('table', { name: '公募基金榜单' }).waitFor()
  passed('Original Vue route opens the native fund ranking')

  const follow = page.getByRole('button', { name: /^关注：/ }).first()
  const fundName = (await follow.getAttribute('aria-label')).replace(/^关注：/, '')
  await follow.click()
  await page.getByRole('button', { name: `取消关注：${fundName}`, exact: true }).waitFor()
  await page.locator('.side-nav').getByRole('link', { name: '我的自选', exact: true }).click()
  await page.getByRole('heading', { level: 1, name: '我的自选', exact: true }).waitFor()
  await page.getByRole('table', { name: '自选列表' }).getByText(fundName, { exact: true }).waitFor()
  await page.reload()
  await page.getByRole('table', { name: '自选列表' }).getByText(fundName, { exact: true }).waitFor()
  passed('Fund follow state reaches Watchlist and survives refresh')
  await page.getByRole('textbox', { name: '搜索名称或代码', exact: true }).fill('不存在的标的验收')
  await page.getByText('暂无匹配的自选标的', { exact: true }).waitFor()
  await page.getByRole('button', { name: '重置筛选', exact: true }).click()
  await page.getByRole('table', { name: '自选列表' }).getByText(fundName, { exact: true }).waitFor()
  passed('Watchlist search and reset recover from an empty result')

  await visit('/', '资产总览')
  await page.getByRole('button', { name: '录入持仓', exact: true }).click()
  let dialog = page.getByRole('dialog', { name: '录入持仓', exact: true })
  await dialog.getByLabel('标的名称', { exact: true }).fill('迁移验收基金')
  await dialog.getByLabel('六位代码', { exact: true }).fill('159999')
  await dialog.getByLabel('持仓数量', { exact: true }).fill('100')
  await dialog.getByLabel('当前价格 / 净值', { exact: true }).fill('1.2')
  await dialog.getByLabel('平均成本', { exact: true }).fill('1')
  await dialog.getByLabel('昨收价 / 净值', { exact: true }).fill('1.1')
  await dialog.getByRole('button', { name: '保存持仓', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })
  await page.getByRole('table', { name: '持仓明细' }).getByText('迁移验收基金', { exact: true }).waitFor()
  await page.reload()
  await page.getByRole('table', { name: '持仓明细' }).getByText('迁移验收基金', { exact: true }).waitFor()
  passed('Holding entry persists across a full page refresh')

  const cashBefore = await page.evaluate(() => JSON.parse(localStorage.getItem('guanlan.portfolio.v1')).cash)
  await visit('/ledger', '交易账本')
  await page.getByRole('button', { name: '新增交易', exact: true }).click()
  dialog = page.getByRole('dialog', { name: '新增交易记录', exact: true })
  await dialog.getByLabel('六位代码', { exact: true }).fill('159999')
  await dialog.getByLabel('标的名称', { exact: true }).fill('迁移验收基金')
  await dialog.getByLabel('成交价格', { exact: true }).fill('1.3')
  await dialog.getByLabel('成交数量', { exact: true }).fill('10')
  await dialog.getByLabel('手续费', { exact: true }).fill('1')
  await dialog.getByRole('button', { name: '确认记账', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })
  const portfolio = await page.evaluate(() => JSON.parse(localStorage.getItem('guanlan.portfolio.v1')))
  assert.equal(portfolio.cash, cashBefore - 14)
  assert.equal(portfolio.holdings.find((item) => item.code === '159999').quantity, 110)
  assert.equal(portfolio.transactions[0].code, '159999')
  await visit('/portfolio', '组合分析')
  await page.getByText('迁移验收基金', { exact: true }).first().waitFor()
  passed('Ledger entry reconciles cash and holding quantities across pages')

  await visit('/settings?section=profile', '账号与设置')
  await page.getByRole('textbox', { name: '显示名称', exact: true }).fill('验收研究员')
  await page.getByRole('button', { name: '保存更改', exact: true }).click()
  await page.locator('.admin-name').getByText('验收研究员', { exact: true }).waitFor()
  await page.getByRole('button', { name: '外观设置', exact: true }).click()
  await page.getByRole('combobox', { name: '深色层次', exact: true }).selectOption('soft')
  await page.getByRole('button', { name: '保存更改', exact: true }).click()
  await page.reload()
  await page.locator('.admin-name').getByText('验收研究员', { exact: true }).waitFor()
  assert.equal(await page.locator('html').getAttribute('data-appearance'), 'soft')
  passed('Display name and appearance are saved and restored')

  await visit('/settings?section=models', '账号与设置')
  assert.equal(await page.getByRole('button', { name: '读取可用模型并保存连接', exact: true }).isEnabled(), false)
  await page.getByText('后端服务尚未接入，可先保存模型选择，接入后再验证连接。', { exact: true }).waitFor()
  passed('Unconfigured model backend is explicit and discovery stays disabled')
  assert.deepEqual(errors, [], 'Browser console or runtime errors')
  assert.deepEqual(
    externalRequests.filter((url) => !isTemplateFont(url)),
    [],
    'Only the original template font requests are allowed',
  )
  passed('No browser errors; external requests are limited to the original template fonts')
} catch (error) {
  if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {})
  errors.push(error.stack || String(error))
  console.error(error)
  process.exitCode = 1
} finally {
  await writeFile(
    path.join(output, 'results.json'),
    JSON.stringify({ results, errors, externalRequests, fontWarnings }, null, 2) + '\n',
  )
  await browser?.close()
  await new Promise((resolve, reject) => server.httpServer.close((error) => (error ? reject(error) : resolve())))
}
console.log(`${results.length} browser checks; ${errors.length} errors. Evidence: ${output}`)
