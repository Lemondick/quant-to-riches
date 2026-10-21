import { test, expect } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * batch4 流 B（#6）：前端冒烟测试。
 * 通过标准：“页面能渲染、无控制台报错（console.error / pageerror）”。
 * - SMOKE_MODE=demo（默认）：离线演示构建，覆盖仪表盘 / 持仓页 / 风险看板 / 组合回测页。
 * - SMOKE_MODE=live：VITE_API_BASE_URL 指向 mock-api（401 session），覆盖登录页。
 * 截图留档：tests/e2e/screenshots/<mode>/<name>.png
 */
const mode = process.env.SMOKE_MODE === 'live' ? 'live' : 'demo'
const shotDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'screenshots', mode)

test.beforeEach(async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => pageErrors.push(String(error)))
  // @ts-expect-error 透传给 afterEach 断言
  testInfo.consoleErrors = consoleErrors
  // @ts-expect-error 透传给 afterEach 断言
  testInfo.pageErrors = pageErrors
})

test.afterEach(async ({ page }, testInfo) => {
  // @ts-expect-error 见 beforeEach
  const consoleErrors = testInfo.consoleErrors as string[]
  // @ts-expect-error 见 beforeEach
  const pageErrors = testInfo.pageErrors as string[]
  await page.screenshot({ path: path.join(shotDir, `${testInfo.title.replace(/[^\w\u4e00-\u9fa5-]+/g, '_')}.png`) })
  expect(pageErrors, `页面异常: ${pageErrors.join('\n')}`).toEqual([])
  expect(consoleErrors, `控制台报错: ${consoleErrors.join('\n')}`).toEqual([])
})

async function gotoOk(page, url: string) {
  const response = await page.goto(url, { waitUntil: 'domcontentloaded' })
  expect(response?.status(), `${url} HTTP 状态`).toBe(200)
  await expect(page.locator('.page-shell')).toBeVisible()
}

test.describe('demo 模式：页面渲染', () => {
  test.skip(mode !== 'demo', '仅 demo 模式运行')

  test('仪表盘 /', async ({ page }) => {
    await gotoOk(page, '/')
    await expect(page.getByText('资产总览', { exact: true }).first()).toBeVisible()
  })

  test('持仓页 /ledger', async ({ page }) => {
    await gotoOk(page, '/ledger')
    await expect(page.getByText('交易账本', { exact: true }).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: '交易记录' })).toBeVisible()
  })

  test('风险看板 /risk', async ({ page }) => {
    await gotoOk(page, '/risk')
    await expect(page.getByText('风险看板', { exact: true }).first()).toBeVisible()
  })

  test('组合回测页 /portfolio-backtest', async ({ page }) => {
    await gotoOk(page, '/portfolio-backtest')
    await expect(page.getByText('组合回测', { exact: true }).first()).toBeVisible()
  })
})

test.describe('live 模式：登录页', () => {
  test.skip(mode !== 'live', '仅 live 模式运行（需 mock-api 返回 401 session）')

  test('登录页 /（未登录显示登录表单）', async ({ page }) => {
    await gotoOk(page, '/')
    const form = page.getByRole('form', { name: '登录后端账户' })
    await expect(form).toBeVisible()
    await expect(form.getByLabel('用户名')).toBeVisible()
    await expect(form.getByLabel('密码')).toBeVisible()
  })
})
