// Run with: npm run test:browser:contracts
// Local browser + synthetic HTTP responses; no application database or paid services.
import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const origin = 'http://127.0.0.1:57572'
let server, browser
before(async () => {
  server = await createServer({
    define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify(origin) },
    server: { host: '127.0.0.1', port: 57572, strictPort: true },
  })
  await server.listen()
  browser = await chromium.launch({ headless: true })
})
after(async () => {
  await browser?.close()
  await server?.close()
})

const session = {
  user: { id: 'synthetic-alice', username: 'synthetic-alice', role: 'user' },
  csrf_token: 'csrf',
  cache_scope: 'scope-alice',
}
const goal = {
  id: 'g1',
  name: '合成教育金',
  target_amount: '500000',
  target_date: '2031-09-01',
  current_amount: '86000',
  assumed_annual_return: '0.06',
}
const projection = {
  required_monthly: 4321,
  projected_amount: 120000,
  gap: 380000,
  on_track: false,
  assumptions: { assumed_annual_return: 0.06 },
  disclaimer: '假设测算非承诺',
  as_of_net_worth: '90000',
}
const tracking = {
  missed_alert_days: 3,
  plans: [
    {
      plan_id: 'p1',
      symbol: '000001',
      frequency: 'monthly',
      periods: [{ due_date: '2026-10-01', state: 'on_time', planned_amount: '3000', actual_amount: '3100' }],
    },
    {
      plan_id: 'p2',
      symbol: '000002',
      frequency: 'weekly',
      periods: [{ due_date: '2026-10-22', state: 'missed', planned_amount: '500', actual_amount: null }],
    },
  ],
}

async function pageFor(t, handler) {
  const context = await browser.newContext()
  const page = await context.newPage(),
    calls = [],
    failures = []
  page.on('pageerror', (error) => failures.push(error.stack ?? error.message))
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request(),
      url = new URL(request.url())
    const call = {
      path: url.pathname.replace('/api/v1', '') + url.search,
      method: request.method(),
      body: request.postDataJSON(),
    }
    calls.push(call)
    const response = (await handler(call)) ?? {
      body:
        call.path === '/auth/session'
          ? session
          : call.path === '/sentiment'
            ? { cached: true, result: { items: [], warning: '合规情绪数据源未接入' } }
            : {},
    }
    await route.fulfill({ status: response.status ?? 200, json: response.body })
  })
  t.after(async () => {
    await context.close()
    assert.deepEqual(failures, [], 'browser exceptions')
  })
  return { page, calls }
}
const visible = (locator) => locator.waitFor({ state: 'visible', timeout: 8000 })

for (const change of ['switch', 'logout']) {
  test(`ask clears account history and draft after ${change}`, async (t) => {
    let current = session
    const { page } = await pageFor(t, ({ path }) => {
      if (path === '/auth/session')
        return current ? { body: current } : { status: 401, body: { error_code: 'authentication_required' } }
      if (path === '/goals') return { body: { items: [] } }
      if (path === '/ask')
        return {
          body: { answer: '合成账户 A 的私有回答', matched: true, template: 'position_pnl_ranking', model_calls: 0 },
        }
    })
    await page.goto(origin + '/goals')
    await page.getByRole('button', { name: '打开智能问答' }).click()
    await page.getByRole('button', { name: '盈亏排名', exact: true }).click()
    await visible(page.getByText('合成账户 A 的私有回答', { exact: true }))
    await page.getByRole('textbox', { name: '输入问题', exact: true }).fill('合成账户 A 的草稿')

    const checked = page.waitForResponse((response) => response.url().endsWith('/api/v1/auth/session'))
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await checked
    assert.equal(await page.getByText('合成账户 A 的私有回答', { exact: true }).count(), 1)
    await visible(page.getByRole('textbox', { name: '输入问题', exact: true }))
    assert.equal(await page.getByRole('textbox', { name: '输入问题', exact: true }).inputValue(), '合成账户 A 的草稿')

    current =
      change === 'logout'
        ? null
        : { ...session, user: { ...session.user, id: 'synthetic-bob' }, cache_scope: 'scope-bob' }
    const changed = page.waitForResponse((response) => response.url().endsWith('/api/v1/auth/session'))
    await page.evaluate(() => window.dispatchEvent(new Event('quant-auth-invalidated')))
    await changed
    await page.waitForFunction(() => !document.body.textContent.includes('合成账户 A 的私有回答'), undefined, {
      timeout: 2000,
    })
    if (await page.getByRole('button', { name: '打开智能问答' }).count())
      await page.getByRole('button', { name: '打开智能问答' }).click()
    assert.equal(await page.getByText('合成账户 A 的私有回答', { exact: true }).count(), 0)
    if (change === 'switch') {
      await visible(page.getByRole('textbox', { name: '输入问题', exact: true }))
      assert.equal(await page.getByRole('textbox', { name: '输入问题', exact: true }).inputValue(), '')
    } else assert.equal(await page.getByRole('textbox', { name: '输入问题', exact: true }).count(), 0)
  })
}

test('ask hides private content while checking the same session and restores it after verification', async (t) => {
  let verifying = false,
    release
  const checked = new Promise((resolve) => {
    release = resolve
  })
  const { page } = await pageFor(t, async ({ path }) => {
    if (path === '/auth/session' && verifying) {
      await checked
      return { body: session }
    }
    if (path === '/goals') return { body: { items: [] } }
    if (path === '/ask')
      return { body: { answer: '同一账户的已验证回答', matched: true, template: 'cash_ratio', model_calls: 0 } }
  })
  t.after(() => release())
  await page.goto(origin + '/goals')
  await page.getByRole('button', { name: '打开智能问答' }).click()
  await page.getByRole('button', { name: '现金比例', exact: true }).click()
  await visible(page.getByText('同一账户的已验证回答', { exact: true }))
  await page.getByRole('textbox', { name: '输入问题', exact: true }).fill('同一账户的草稿')
  verifying = true
  try {
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await page.waitForFunction(() => !document.body.textContent.includes('同一账户的已验证回答'), undefined, {
      timeout: 2000,
    })
    assert.equal(await page.getByRole('textbox', { name: '输入问题', exact: true }).count(), 0)
  } finally {
    release()
  }
  await visible(page.getByText('同一账户的已验证回答', { exact: true }))
  assert.equal(await page.getByRole('textbox', { name: '输入问题', exact: true }).inputValue(), '同一账户的草稿')
})

for (const outcome of ['success', 'failure']) {
  test(`ask ignores a prior session's late ${outcome} response`, async (t) => {
    let current = session,
      release,
      received,
      finished
    const responseGate = new Promise((resolve) => {
      release = resolve
    })
    const requestStarted = new Promise((resolve) => {
      received = resolve
    })
    const responseFinished = new Promise((resolve) => {
      finished = resolve
    })
    const { page } = await pageFor(t, ({ path }) => {
      if (path === '/auth/session') return { body: current }
      if (path === '/goals') return { body: { items: [] } }
    })
    t.after(() => release())
    let first = true
    await page.route('**/api/v1/ask', async (route) => {
      if (!first)
        return route.fulfill({
          json: { answer: '合成账户 B 的新回答', matched: true, template: 'cash_ratio', model_calls: 0 },
        })
      first = false
      received()
      await responseGate
      try {
        await route.fulfill(
          outcome === 'success'
            ? { json: { answer: '合成账户 A 的迟到私有回答', matched: true, template: 'year_return', model_calls: 0 } }
            : { status: 503, json: { error_code: 'worker_unavailable' } },
        )
      } finally {
        finished()
      }
    })
    await page.goto(origin + '/goals')
    await page.getByRole('button', { name: '打开智能问答' }).click()
    await page.getByRole('button', { name: '今年收益', exact: true }).click()
    await requestStarted
    current = { ...session, user: { ...session.user, id: 'synthetic-bob' }, cache_scope: 'scope-bob' }
    const changed = page.waitForResponse((response) => response.url().endsWith('/api/v1/auth/session'))
    await page.evaluate(() => window.dispatchEvent(new Event('quant-auth-invalidated')))
    await changed
    release()
    await responseFinished
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    if (await page.getByRole('button', { name: '打开智能问答' }).count())
      await page.getByRole('button', { name: '打开智能问答' }).click()
    assert.equal(await page.getByText('合成账户 A 的迟到私有回答', { exact: true }).count(), 0)
    assert.equal(await page.getByText('数据暂不可用，后台准备完成后可再次刷新。', { exact: true }).count(), 0)
    await page.getByRole('button', { name: '现金比例', exact: true }).click()
    await visible(page.getByText('合成账户 B 的新回答', { exact: true }))
  })
}

test('risk chart draws nonempty NAV and negative drawdowns with readable tooltips at both widths', async (t) => {
  const { page } = await pageFor(t, ({ path }) => {
    if (path === '/risk/metrics')
      return {
        body: {
          cached: true,
          result: {
            var95: -0.02,
            max_drawdown: -0.1,
            ann_volatility: 0.15,
            sharpe: 0.8,
            calmar: 0.6,
            period_days: 5,
            method: 'historical_simulation',
            computed_at: '2026-10-23',
            nav_series: [1.1, 1.05, 1.02, 0.99, 1.08].map((nav, index) => ({ date: `2026-10-${19 + index}`, nav })),
          },
        },
      }
    if (path === '/risk/stress') return { body: { scenarios: [] } }
  })
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.goto(origin + '/risk')
    const chart = page.getByRole('img', { name: '组合净值与回撤走势' })
    await visible(chart.locator('canvas').first())
    const box = await chart.boundingBox()
    assert.ok(box.width > 200 && box.height >= 300)
    await chart.hover({ position: { x: box.width * 0.55, y: box.height * 0.5 } })
    const tooltip = chart.getByText(/净值 \d+\.\d{4}/).last()
    await visible(tooltip)
    assert.match(await tooltip.innerText(), /回撤 -\d+\.\d{2}%/)
    assert.equal(await page.getByText('图表暂时无法加载，请刷新页面重试。', { exact: true }).count(), 0)
    if (process.env.QUANT_CHART_SCREENSHOTS)
      await page.screenshot({ path: `${process.env.QUANT_CHART_SCREENSHOTS}/risk-${width}.png`, fullPage: true })
  }
})

test('live goals binds paged rows and POST projections, including ledger amount', async (t) => {
  const { page, calls } = await pageFor(t, ({ path }) =>
    path === '/goals' ? { body: { items: [goal] } } : path.endsWith('/project') ? { body: projection } : undefined,
  )
  await page.goto(origin + '/goals')
  await visible(page.getByRole('heading', { name: goal.name }))
  await visible(page.locator('.goal-card').getByText('¥ 90,000.00', { exact: true }))
  assert.match(await page.locator('.goal-card').innerText(), /4,321/)
  assert.deepEqual(
    calls.filter((call) => call.path.endsWith('/project')),
    [{ path: '/goals/g1/project', method: 'POST', body: {} }],
  )
})

test('unavailable goal projection stays unavailable without a fabricated zero contribution', async (t) => {
  const { page } = await pageFor(t, ({ path }) =>
    path === '/goals'
      ? { body: { items: [goal] } }
      : path.endsWith('/project')
        ? { status: 503, body: { error_code: 'unavailable' } }
        : undefined,
  )
  await page.goto(origin + '/goals')
  await visible(page.getByRole('heading', { name: goal.name }))
  await visible(page.locator('.goal-card').getByText('测算暂不可用', { exact: true }))
  assert.doesNotMatch(await page.locator('.goal-card').innerText(), /每月需投/)
})

test('empty live goals and DCA plans render without exceptions', async (t) => {
  const { page } = await pageFor(t, ({ path }) =>
    path === '/goals'
      ? { body: { items: [] } }
      : path === '/dca/tracking'
        ? { body: { missed_alert_days: 3, plans: [] } }
        : undefined,
  )
  await page.goto(origin + '/goals')
  await visible(page.getByText('暂无目标，点击右上角「新建目标」创建第一个目标。', { exact: true }))
  await page.goto(origin + '/dca-tracking')
  await visible(page.getByText('暂无可跟踪的定投计划。', { exact: true }))
})

test('DCA selects a real plan and registers against the selected plan ID', async (t) => {
  const { page, calls } = await pageFor(t, ({ path }) =>
    path === '/dca/tracking'
      ? { body: tracking }
      : path === '/dca/executions'
        ? { body: { id: 'execution-1' } }
        : undefined,
  )
  await page.goto(origin + '/dca-tracking')
  await visible(page.getByRole('combobox', { name: '定投计划', exact: true }))
  await page.getByRole('combobox', { name: '定投计划', exact: true }).selectOption('p2')
  await visible(page.getByText('000002 · 每周 · 按期/漏投/金额偏离', { exact: true }))
  await page.getByRole('button', { name: '登记扣款', exact: true }).click()
  await page.getByRole('dialog').getByLabel('扣款日期', { exact: true }).fill('2026-10-22')
  await page.getByRole('dialog').getByLabel('实际扣款金额（元）', { exact: true }).fill('500')
  await page.getByRole('dialog').getByLabel('备注（可选）', { exact: true }).fill('合成记录')
  await page.getByRole('button', { name: '确认登记', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.deepEqual(
    calls.find((call) => call.path === '/dca/executions'),
    {
      path: '/dca/executions',
      method: 'POST',
      body: { plan_id: 'p2', due_date: '2026-10-22', actual_amount: 500, note: '合成记录' },
    },
  )
})

test('live 404 responses never become demo goals, DCA, or net worth', async (t) => {
  const { page } = await pageFor(t, ({ path }) =>
    path === '/auth/session' ? undefined : { status: 404, body: { error_code: 'missing' } },
  )
  for (const path of ['/goals', '/dca-tracking', '/net-worth']) {
    await page.goto(origin + path)
    await visible(page.getByRole('alert').filter({ hasText: 'HTTP 404' }).first())
    assert.doesNotMatch(await page.locator('main').innerText(), /契约演示|演示模式|每月需投/)
  }
})

test('failed goal creation remains an unsaved form', async (t) => {
  const { page, calls } = await pageFor(t, ({ path, method }) =>
    path === '/goals'
      ? method === 'GET'
        ? { body: { items: [] } }
        : { status: 404, body: { error_code: 'missing' } }
      : undefined,
  )
  await page.goto(origin + '/goals')
  await visible(page.getByText('暂无目标，点击右上角「新建目标」创建第一个目标。', { exact: true }))
  await page.getByRole('button', { name: '新建目标', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('目标名称').fill('未保存合成目标')
  await dialog.getByLabel('目标日期').fill('2031-09-01')
  await dialog.getByLabel('目标金额（元）', { exact: true }).fill('500000')
  await dialog.getByRole('button', { name: '创建目标', exact: true }).click()
  await visible(dialog.getByRole('alert').filter({ hasText: 'HTTP 404' }))
  assert.equal(await page.locator('.goal-card').count(), 0)
  assert.deepEqual(calls.find((call) => call.method === 'POST' && call.path === '/goals')?.body, {
    name: '未保存合成目标',
    target_amount: 500000,
    target_date: '2031-09-01',
    assumed_annual_return: 0.06,
  })
})

test('goal data disappears while a different account loads', async (t) => {
  let bob = false,
    release,
    requested
  const gate = new Promise((resolve) => {
    release = resolve
  })
  const pending = new Promise((resolve) => {
    requested = resolve
  })
  const { page } = await pageFor(t, async ({ path }) => {
    if (path === '/auth/session' && bob)
      return {
        body: {
          ...session,
          user: { ...session.user, id: 'synthetic-bob', username: 'synthetic-bob' },
          cache_scope: 'scope-bob',
        },
      }
    if (path === '/goals') {
      if (bob) {
        requested()
        await gate
        return { body: { items: [{ ...goal, id: 'g2', name: 'Bob 合成目标' }] } }
      }
      return { body: { items: [goal] } }
    }
    if (path.endsWith('/project')) return { body: projection }
  })
  await page.goto(origin + '/goals')
  await visible(page.getByRole('heading', { name: goal.name }))
  bob = true
  await page.evaluate(() => globalThis.dispatchEvent(new Event('focus')))
  await pending
  assert.equal(await page.getByRole('heading', { name: goal.name }).count(), 0)
  release()
  await visible(page.getByRole('heading', { name: 'Bob 合成目标' }))
})

test('CNY holdings and live net worth widgets render actual contracts without an FX quote', async (t) => {
  const { page } = await pageFor(t, ({ path }) => {
    if (path === '/dashboard/summary?separateNav=true')
      return {
        body: {
          user_id: session.user.id,
          generated_at: '2026-10-22',
          unavailable: [],
          portfolio: {
            positions: [
              {
                symbol: '000001',
                asset_type: 'fund',
                name: '人民币合成持仓',
                quantity: '100000',
                average_cost: '1',
                market_value: '120000',
                as_of_date: '2026-10-22',
              },
            ],
            total_value: '120000',
            cash: '0',
            as_of_date: '2026-10-22',
          },
          preferences: { watchlist: [] },
          valuations: { items: [], unavailable: [] },
          quotes: {
            items: [
              {
                symbol: '000001',
                asset_type: 'fund',
                name: '人民币合成持仓',
                price: '1.2',
                currency: 'CNY',
                change_rate: '0',
                quality_status: 'valid',
                is_stale: false,
              },
            ],
            unavailable: [],
          },
        },
      }
    if (path.startsWith('/portfolio/history')) return { body: { points: [] } }
    if (path.startsWith('/net-worth?'))
      return {
        body: {
          total_assets: '120000',
          net_worth: '120000',
          liabilities_note: '系统暂无负债登记，负债按 0 计',
          as_of_date: '2026-10-22',
          history: [
            { date: '2026-10-21', total_value: '100000' },
            { date: '2026-10-22', total_value: '120000' },
          ],
        },
      }
    if (path === '/net-worth/allocation')
      return {
        body: {
          items: [
            { name: '基金', value: '80000' },
            { name: '股票', value: '40000' },
          ],
        },
      }
    if (path === '/valuation')
      return {
        body: {
          cached: true,
          result: {
            as_of: '2026-10-22',
            items: [],
            temperature: { score: null, label: '数据不足' },
            warnings: ['PE/PB 历史数据源未接入'],
          },
        },
      }
    if (path === '/risk/metrics')
      return {
        body: {
          cached: false,
          result: {
            var95: 0.023,
            max_drawdown: 0.12,
            ann_volatility: 0.17,
            sharpe: 1.1,
            calmar: 0.8,
            period_days: 365,
            method: 'historical_simulation',
            computed_at: '2026-10-22',
          },
        },
      }
  })
  await page.goto(origin + '/')
  await visible(page.getByText('¥ 120,000.00 · 本币原值', { exact: true }))
  await visible(page.locator('.widget-total').getByText('¥ 120,000.00', { exact: true }))
  await visible(page.getByText('PE/PB 历史数据源未接入', { exact: true }))
  assert.match(await page.locator('.widget-bars').innerText(), /基金[\s\S]*66.7%[\s\S]*股票[\s\S]*33.3%/)
  assert.equal(await page.getByRole('img', { name: /市场温度计/ }).count(), 0)
  await page.goto(origin + '/net-worth')
  await visible(page.getByText('系统暂无负债登记，负债按 0 计', { exact: true }))
  assert.match(await page.locator('.donut-panel').innerText(), /基金[\s\S]*股票/)
})

test('unconfigured rebalance and missing valuation data have explicit states', async (t) => {
  const { page } = await pageFor(t, ({ path }) =>
    path === '/rebalance/targets'
      ? { body: { targets: null } }
      : path === '/valuation'
        ? {
            body: {
              cached: true,
              result: {
                as_of: '2026-10-22',
                items: [],
                temperature: { score: null, label: '数据不足' },
                warnings: ['PE/PB 历史数据源未接入'],
              },
            },
          }
        : undefined,
  )
  await page.goto(origin + '/rebalance')
  await visible(page.getByText('尚未设置目标配置，请先保存目标比例后评估。', { exact: true }))
  assert.equal(await page.getByText('当前配置在偏离阈值范围内，无需调仓。', { exact: true }).count(), 0)
  await page.goto(origin + '/valuation')
  await visible(page.getByText('PE/PB 历史数据源未接入', { exact: true }))
  assert.equal(await page.getByRole('img', { name: /市场温度计/ }).count(), 0)
})

test('successful live backtest renders actual worker metrics, null XIRR and warnings', async (t) => {
  const { page } = await pageFor(t, ({ path }) =>
    path === '/backtest/portfolio'
      ? {
          body: {
            metrics: {
              total_return: -0.0015,
              xirr: null,
              max_drawdown: -0.0015,
              sharpe_zero_rate: -0.4,
              total_invested: '200',
              final_value: '199.70',
            },
            curve: [
              { date: '2026-01-01', value: '99.85', invested: '100', unit_nav: 1 },
              { date: '2026-02-01', value: '199.70', invested: '200', unit_nav: 0.9985 },
            ],
            rebalance_events: [{ date: '2026-02-01', action: 'rebalance_to_target', reason: 'period:1M 到期再平衡' }],
            warnings: ['组合再平衡的赎回端未计赎回费'],
          },
        }
      : undefined,
  )
  await page.goto(origin + '/portfolio-backtest')
  await page.getByRole('button', { name: '开始回测', exact: true }).click()
  await visible(page.getByText('组合再平衡的赎回端未计赎回费', { exact: true }))
  assert.match(await page.locator('.metric-card').nth(0).innerText(), /累计收益[\s\S]*-0.15%/)
  assert.match(await page.locator('.metric-card').nth(1).innerText(), /XIRR 年化[\s\S]*—/)
  assert.match(await page.locator('.row-list').innerText(), /累计投入[\s\S]*¥200[\s\S]*期末市值[\s\S]*¥199.7/)
  await visible(page.getByText('period:1M 到期再平衡', { exact: true }))
})

for (const change of ['switch', 'logout']) {
  test(`backtest hides prior account inputs and results during ${change}`, async (t) => {
    let changed = false,
      release
    const verification = new Promise((resolve) => {
      release = resolve
    })
    const { page } = await pageFor(t, async ({ path }) => {
      if (path === '/auth/session' && changed) {
        await verification
        return change === 'logout'
          ? { status: 401, body: { error_code: 'authentication_required' } }
          : { body: { ...session, user: { ...session.user, id: 'synthetic-bob' }, cache_scope: 'scope-bob' } }
      }
      if (path === '/backtest/portfolio')
        return {
          body: {
            metrics: {
              total_return: 0.1,
              xirr: null,
              max_drawdown: 0,
              sharpe_zero_rate: null,
              total_invested: '100',
              final_value: '110',
            },
            curve: [],
            rebalance_events: [],
            warnings: ['合成账户专属回测'],
          },
        }
    })
    await page.goto(origin + '/portfolio-backtest')
    await page.getByRole('button', { name: '开始回测', exact: true }).click()
    await visible(page.getByText('合成账户专属回测', { exact: true }))
    await page.getByLabel('基金名称', { exact: true }).first().fill('账户私有组合名称')
    changed = true
    try {
      await page.evaluate(() => globalThis.window.dispatchEvent(new Event('focus')))
      await page.waitForFunction(() => !globalThis.document.body.textContent.includes('合成账户专属回测'), undefined, {
        timeout: 2000,
      })
      assert.equal(await page.getByLabel('基金名称', { exact: true }).count(), 0)
    } finally {
      release()
    }
    if (change === 'switch') {
      await visible(page.getByRole('button', { name: '开始回测', exact: true }))
      assert.notEqual(await page.getByLabel('基金名称', { exact: true }).first().inputValue(), '账户私有组合名称')
    } else await visible(page.getByRole('form', { name: '登录后端账户' }))
    assert.equal(await page.getByText('合成账户专属回测', { exact: true }).count(), 0)
  })
}

test('live rebalance displays server gradual advice and respects exact threshold', async (t) => {
  let atThreshold = false
  const targets = {
    stock_ratio: '0.6',
    bond_ratio: '0.3',
    cash_ratio: '0.1',
    industry_concentration_limit: '0.3',
    deviation_threshold: '0.05',
    updated_at: '2026-10-22',
  }
  const { page } = await pageFor(t, ({ path }) => {
    if (path === '/rebalance/targets') return { body: { targets } }
    if (path === '/rebalance/evaluate')
      return {
        body: {
          as_of: '2026-10-22',
          total_value: '10000',
          targets,
          allocation: { stock: atThreshold ? '6500' : '8000', bond: atThreshold ? '2500' : '1000', cash: '1000' },
          industry: { top: null, ratio: '0', breached: false },
          drifts: ['stock', 'bond', 'cash'].map((asset_class) => ({
            asset_class,
            breached: !atThreshold && asset_class !== 'cash',
          })),
          breached: !atThreshold,
          suggestions: atThreshold ? [] : [{ note: '卖出股票类资产约 1000 元' }, { note: '买入债券类资产约 1000 元' }],
        },
      }
  })
  await page.goto(origin + '/rebalance')
  await visible(page.getByText('卖出股票类资产约 1000 元', { exact: true }))
  await visible(page.getByText('买入债券类资产约 1000 元', { exact: true }))
  assert.doesNotMatch(
    await page
      .locator('.suggestion')
      .allTextContents()
      .then((items) => items.join(' ')),
    /2,000|2000/,
  )
  atThreshold = true
  await page.reload()
  await visible(page.getByText('当前配置在偏离阈值范围内，无需调仓。', { exact: true }))
  assert.equal(await page.getByText('超阈值', { exact: true }).count(), 0)
})

test('failed live backtest reports the HTTP failure without a demo result', async (t) => {
  const { page } = await pageFor(t, ({ path }) =>
    path === '/backtest/portfolio' ? { status: 404, body: { error_code: 'missing' } } : undefined,
  )
  await page.goto(origin + '/portfolio-backtest')
  await page.getByRole('button', { name: '开始回测', exact: true }).click()
  await visible(page.getByRole('alert').filter({ hasText: 'HTTP 404' }))
  assert.doesNotMatch(await page.locator('main').innerText(), /演示回测|累计收益|期末市值/)
})

for (const code of ['123456', 'ABCD-EFGH']) {
  test(`browser finishes pending-token login with ${code.length === 6 ? 'OTP' : 'recovery code'}`, async (t) => {
    let authenticated = false
    const { page, calls } = await pageFor(t, ({ path }) => {
      if (path === '/auth/session')
        return authenticated ? undefined : { status: 401, body: { error_code: 'authentication_required' } }
      if (path === '/auth/login') return { body: { two_factor_required: true, pending_token: 'synthetic-challenge' } }
      if (path === '/auth/2fa/verify') {
        authenticated = true
        return { body: { ...session, expires_at: '2026-10-23T00:00:00Z' } }
      }
      if (path === '/goals') return { body: { items: [] } }
    })
    await page.goto(origin + '/goals')
    const form = page.getByRole('form', { name: '登录后端账户' })
    await form.getByLabel('用户名').fill('synthetic-alice')
    await form.getByLabel('密码').fill('synthetic-password')
    await form.getByRole('button', { name: '登录', exact: true }).click()
    await visible(form.getByLabel('验证码或恢复码'))
    assert.equal(authenticated, false)
    assert.equal(calls.filter((call) => call.path === '/goals').length, 0)
    await form.getByLabel('验证码或恢复码').fill(code)
    await form.getByRole('button', { name: '验证并登录', exact: true }).click()
    await visible(page.getByText('暂无目标，点击右上角「新建目标」创建第一个目标。', { exact: true }))
    assert.deepEqual(calls.find((call) => call.path === '/auth/2fa/verify')?.body, {
      pending_token: 'synthetic-challenge',
      code,
    })
  })
}
