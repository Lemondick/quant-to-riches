import test from 'node:test'
import assert from 'node:assert/strict'
import { createDataApi } from '../src/services/dataApi.ts'
import * as batch2 from '../src/services/batch2Api.ts'
import * as batch3 from '../src/services/batch3Api.ts'
import * as batch5 from '../src/services/batch5Api.ts'

function client(routes) {
  const api = createDataApi('https://quant.test', async (url, init) => {
    const path = url.replace('https://quant.test/api/v1', '')
    const route = routes[init.method + ' ' + path]
    assert.ok(route, 'unexpected request: ' + init.method + ' ' + path)
    return Response.json(typeof route === 'function' ? route(init.body && JSON.parse(init.body)) : route)
  })
  return { read: api.read, post: (path, body = {}) => api.write(path, body, 'csrf', 'scope') }
}
const targets = {
  stock_ratio: '0.6',
  bond_ratio: '0.3',
  cash_ratio: '0.1',
  industry_concentration_limit: '0.3',
  deviation_threshold: '0.05',
  updated_at: '2026-10-22T08:00:00Z',
}

test('rebalance reads targets and POST evaluation, converts fractions and sends ratios on save', async () => {
  const state = await batch2.loadRebalance(
    client({
      'GET /rebalance/targets': { targets },
      'POST /rebalance/evaluate': {
        as_of: '2026-10-22T08:00:00Z',
        total_value: '10000',
        allocation: { stock: '8000', bond: '1000', cash: '1000' },
        targets,
        drifts: [
          { asset_class: 'stock', target: '0.6', actual: '0.8', drift: '0.2', breached: true },
          { asset_class: 'bond', target: '0.3', actual: '0.1', drift: '-0.2', breached: true },
          { asset_class: 'cash', target: '0.1', actual: '0.1', drift: '0', breached: false },
        ],
        industry: { top: '科技', ratio: '0.4', limit: '0.3', breached: true },
        suggestions: [
          { action: 'sell', asset_class: 'stock', amount: '1000', note: '卖出股票类资产约 1000 元' },
          { action: 'buy', asset_class: 'bond', amount: '1000', note: '买入债券类资产约 1000 元' },
        ],
        breached: true,
        alert_raised: true,
        research_only: true,
        automatic_trading: false,
      },
    }),
  )
  assert.equal(state.target.equity_pct, 60)
  assert.deepEqual(state.current.classes, { equity: '80', bond: '10', cash: '10' })
  assert.deepEqual(state.current.industries, [{ name: '科技', pct: '40' }])
  assert.deepEqual(state.suggestions, ['卖出股票类资产约 1000 元', '买入债券类资产约 1000 元'])
  assert.deepEqual(batch2.rebalanceTargetBody(state.target), {
    stock_ratio: 0.6,
    bond_ratio: 0.3,
    cash_ratio: 0.1,
    industry_concentration_limit: 0.3,
    deviation_threshold: 0.05,
  })
})

test('a user without rebalance targets gets an explicit unconfigured form and no fabricated holdings', async () => {
  const state = await batch2.loadRebalance(client({ 'GET /rebalance/targets': { targets: null } }))
  assert.equal(state.configured, false)
  assert.equal(state.current, null)
})

test('valuation unwraps result, preserves missing temperature, converts ERP to percent and keeps warnings', async () => {
  const snapshot = await batch2.loadValuation(
    client({
      'GET /valuation': {
        cached: false,
        result: {
          as_of: '2026-10-22',
          items: [{ symbol: '000300', pe: 12.5, pb: 1.2, pe_pct: 23.3, pb_pct: 40, erp: 0.0625 }],
          temperature: { score: 33.3, label: '偏冷' },
          warnings: [],
        },
      },
    }),
  )
  assert.equal(batch2.cheapestIndex(snapshot).name, '沪深300')
  assert.equal(snapshot.items[0].erp, 6.25)
  assert.equal(snapshot.items[0].pe_pct, 23.3)
  const empty = await batch2.loadValuation(
    client({
      'GET /valuation': {
        cached: true,
        result: {
          as_of: '2026-10-22',
          items: [],
          temperature: { score: null, label: '数据不足' },
          warnings: ['PE/PB 历史数据源未接入'],
        },
      },
    }),
  )
  assert.equal(empty.temperature.score, null)
  assert.deepEqual(empty.warnings, ['PE/PB 历史数据源未接入'])
})

test('fund health latest unwraps report and handles a report that has never run', async () => {
  const snapshot = await batch2.loadFundHealth(
    client({
      'GET /funds/health/latest': {
        report: {
          as_of: '2026-10-22',
          reports: [
            {
              code: '000001',
              findings: [{ kind: 'scale_change', severity: 'warn', detail: '规模下降', evidence: '季报' }],
            },
          ],
        },
        cron: 'QUANT_HEALTH_CHECK_CRON',
      },
    }),
  )
  assert.equal(snapshot.reports[0].findings[0].detail, '规模下降')
  assert.equal(
    await batch2.loadFundHealth(
      client({ 'GET /funds/health/latest': { report: null, cron: 'QUANT_HEALTH_CHECK_CRON' } }),
    ),
    null,
  )
})

test('announcements maps paged items including null publication times', async () => {
  const list = await batch2.loadAnnouncements(
    client({
      'GET /announcements?limit=100': {
        items: [
          {
            id: '1',
            fund_code: '000001',
            title: '分红公告',
            kind: 'dividend',
            published_at: '2026-10-22T08:00:00Z',
            source_url: 'https://fund.example/1',
            summary: '分红方案',
            created_at: '2026-10-22T09:00:00Z',
          },
          {
            id: '2',
            fund_code: '000001',
            title: '其他公告',
            kind: 'other',
            published_at: null,
            source_url: '',
            summary: '',
            created_at: '2026-10-22T09:00:00Z',
          },
        ],
        total: 2,
        page: 1,
        limit: 100,
      },
    }),
  )
  assert.deepEqual(
    batch2.sortAnnouncementsDesc(list.announcements).map((item) => item.title),
    ['分红公告', '其他公告'],
  )
  assert.equal(list.total, 2)
})

test('risk unwraps metrics without scaling fractions and scales signed stress loss once', async () => {
  const metrics = await batch3.loadRiskMetrics(
    client({
      'GET /risk/metrics': {
        cached: false,
        result: {
          var95: 0.023,
          max_drawdown: 0.12,
          max_drawdown_peak_date: '2026-01-01',
          max_drawdown_trough_date: '2026-05-01',
          ann_volatility: 0.17,
          sharpe: 1.1,
          calmar: 0.8,
          period_days: 365,
          n_points: 244,
          method: 'historical_simulation',
          computed_at: '2026-10-22T08:00:00Z',
          warnings: [],
        },
      },
    }),
  )
  assert.equal(batch3.fmtRiskPct(metrics.var95), '2.30%')
  const stress = await batch3.loadStressTest(
    client({
      'GET /risk/stress': {
        scenarios: [
          {
            id: 'crash_2015',
            name: '2015年股灾',
            index: '上证综指',
            interval: '2015-06-12 → 2016-01-28',
            index_drop_pct: -0.486,
            beta: 1,
            est_loss_pct: -0.486,
            est_loss_amount: -4860,
            source_note: '公开行情',
          },
        ],
        disclaimer: '假想测算非预测',
        warnings: [],
      },
    }),
  )
  assert.equal(stress.scenarios[0].est_loss_pct, 48.6)
  assert.equal(stress.scenarios[0].index_drop_pct, 48.6)
  assert.equal(stress.scenarios[0].est_loss_amount, 4860)
})

test('cost maps actual item names, price from value/units and nullable returns without inventing fee tiers', async () => {
  const snapshot = await batch3.loadCost(
    client({
      'GET /cost/positions': {
        items: [
          {
            symbol: '000001',
            name: '示例基金',
            quantity: 1000,
            average_cost: 1,
            total_cost: 1000,
            market_value: 1100,
            floating_pnl: 100,
            pnl_rate: 0.1,
            weighted_holding_days: 80.2,
            annualized_return: null,
            redemption_rate: 0.005,
            redemption_fee_estimate: 5.5,
            break_even_nav: 1.005,
            cost_basis: '成本口径',
            fee_basis: '已验证费率',
          },
        ],
        count: 1,
        fee_caliber: 'FundFeeRules',
        tax_note: '税费提示',
        research_only: true,
      },
    }),
  )
  assert.deepEqual(
    [
      snapshot.positions[0].cost_total,
      snapshot.positions[0].price,
      snapshot.positions[0].unrealized_pnl,
      snapshot.positions[0].unrealized_pnl_rate,
    ],
    ['1000', '1.1', '100', '0.1'],
  )
  assert.equal(snapshot.positions[0].annualized_return, null)
  assert.equal(snapshot.positions[0].holding_days, 80.2)
  assert.deepEqual(batch3.redemptionHintText(snapshot.positions[0]), {
    tiers: ['当前适用赎回费率：0.50%；估算赎回费 ¥5.50（已验证费率）'],
    taxNote: '税费提示',
  })
})

test('goals unwrap string-valued rows, POST projection and display refreshed ledger value', async () => {
  const result = await batch5.loadGoals(
    client({
      'GET /goals': {
        items: [
          {
            id: 'g1',
            name: '教育金',
            target_amount: '500000',
            target_date: '2031-09-01',
            current_amount: '86000',
            assumed_annual_return: '0.06',
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-10-21T00:00:00Z',
          },
        ],
      },
      'POST /goals/g1/project': (body) => {
        assert.deepEqual(body, {})
        return {
          required_monthly: 5000,
          projected_amount: 120000,
          gap: 380000,
          on_track: false,
          months_remaining: 59,
          assumptions: {
            assumed_annual_return: 0.06,
            monthly_rate: 0.004868,
            compounding: 'monthly',
            contribution_timing: 'month_end',
            as_of: '2026-10-22',
          },
          warnings: [],
          disclaimer: '假设测算非承诺',
          calculation_source: 'worker',
          goal_id: 'g1',
          as_of_net_worth: '90000',
          expected_progress: 0.1,
          actual_progress: 0.18,
          progress_deviation: 0.08,
          deviation_threshold: 0.1,
          deviation_alert: false,
        }
      },
    }),
  )
  assert.equal(result.goals[0].current_amount, 90000)
  assert.equal(result.goals[0].target_amount, 500000)
  assert.equal(result.projections.g1.required_monthly, 5000)
  assert.deepEqual(await batch5.loadGoals(client({ 'GET /goals': { items: [] } })), {
    goals: [],
    projections: {},
    projectionErrors: [],
  })
})

test('net worth joins summary and allocation preserving real asset class names', async () => {
  const snapshot = await batch5.loadNetWorth(
    client({
      'GET /net-worth?history_days=90': {
        total_assets: '120000',
        liabilities: '0',
        net_worth: '120000',
        liabilities_note: '系统暂无负债登记，负债按 0 计',
        as_of_date: '2026-10-22',
        history: [
          { date: '2026-10-21', total_value: '100000' },
          { date: '2026-10-22', total_value: '120000' },
        ],
      },
      'GET /net-worth/allocation': {
        total: '120000',
        items: [
          { name: '基金', value: '80000', weight: 0.6667 },
          { name: '股票', value: '40000', weight: 0.3333 },
        ],
        caliber: '账本市值',
      },
    }),
  )
  assert.equal(snapshot.net_assets, 120000)
  assert.equal(snapshot.as_of, '2026-10-22')
  assert.deepEqual(
    batch5.allocationDonutItems(snapshot, String).map((slice) => [slice.name, slice.value]),
    [
      ['基金', '80000'],
      ['股票', '40000'],
    ],
  )
  assert.deepEqual(batch5.netWorthObservations(snapshot).series[0].values, [0, 20])
})

test('DCA maps multiple plans and on_time state to done, with numeric amounts', async () => {
  const plans = await batch5.loadDcaTracking(
    client({
      'GET /dca/tracking': {
        missed_alert_days: 3,
        plans: [
          {
            plan_id: 'p1',
            symbol: '000001',
            frequency: 'monthly',
            base_amount: '3000',
            expected_periods: 2,
            on_time: 1,
            missed: 1,
            skipped: 0,
            pending: 0,
            avg_amount_deviation: 0.0333,
            periods: [
              {
                due_date: '2026-09-01',
                state: 'on_time',
                planned_amount: '3000',
                actual_amount: '3100',
                amount_deviation: 0.0333,
              },
              {
                due_date: '2026-10-01',
                state: 'missed',
                planned_amount: '3000',
                actual_amount: null,
                amount_deviation: null,
              },
            ],
          },
          {
            plan_id: 'p2',
            symbol: '000002',
            frequency: 'weekly',
            base_amount: '500',
            expected_periods: 0,
            on_time: 0,
            missed: 0,
            skipped: 0,
            pending: 0,
            avg_amount_deviation: null,
            periods: [],
          },
        ],
      },
    }),
  )
  assert.equal(plans.length, 2)
  assert.equal(plans[0].plan_id, 'p1')
  assert.equal(plans[1].plan_id, 'p2')
  assert.equal(batch5.dcaTrackingStats(plans[0]).amount_deviation, 100)
  assert.equal(batch5.dcaTrackingStats(plans[0]).execution_rate_pct, 50)
  assert.deepEqual(
    await batch5.loadDcaTracking(client({ 'GET /dca/tracking': { missed_alert_days: 3, plans: [] } })),
    [],
  )
})

test('live HTTP errors propagate instead of becoming demo account data', async () => {
  const api = createDataApi('https://quant.test', async () =>
    Response.json({ error_code: 'unavailable' }, { status: 404 }),
  )
  const reader = { read: api.read, post: (path, body) => api.write(path, body, 'csrf', 'scope') }
  for (const load of [
    batch2.loadRebalance,
    batch2.loadValuation,
    batch2.loadFundHealth,
    batch2.loadAnnouncements,
    batch3.loadRiskMetrics,
    batch3.loadCost,
    batch5.loadGoals,
    batch5.loadNetWorth,
    batch5.loadDcaTracking,
  ]) {
    await assert.rejects(
      () => load(reader),
      (failure) => failure.status === 404,
    )
  }
})

test('sentiment unwraps the public compute envelope, including unavailable sources', async () => {
  const snapshot = await batch5.loadSentiment(
    client({ 'GET /sentiment': { cached: true, result: { items: [], warning: '合规情绪数据源未接入' } } }),
  )
  assert.deepEqual(snapshot, { items: [], warning: '合规情绪数据源未接入' })
  assert.equal(batch5.sentimentEmptyReason(snapshot), '合规情绪数据源未接入')
})

test('portfolio backtest adapts worker metrics and unit NAV without treating contributions as returns', () => {
  const result = batch2.normalizePortfolioBacktest({
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
    warnings: ['未计赎回费'],
  })
  assert.deepEqual(result.metrics, {
    total_return_pct: -0.15,
    xirr_pct: null,
    max_drawdown_pct: -0.15,
    sharpe: -0.4,
    total_invested: 200,
    final_value: 199.7,
  })
  assert.deepEqual(batch2.backtestCurveObservations(result).series[0].values, [0, -0.15])
  assert.equal(result.rebalance_events[0].detail, 'period:1M 到期再平衡')
  assert.deepEqual(result.warnings, ['未计赎回费'])
})

test('goal projection errors retain real rows but authentication failures invalidate the load', async () => {
  for (const status of [503, 401, 403]) {
    const api = createDataApi('https://quant.test', async (url) =>
      Response.json(
        url.endsWith('/project')
          ? { error_code: 'unavailable' }
          : {
              items: [
                {
                  id: 'g1',
                  name: '教育金',
                  target_amount: '500000',
                  current_amount: '86000',
                  assumed_annual_return: '0.06',
                  target_date: '2031-09-01',
                },
              ],
            },
        { status: url.endsWith('/project') ? status : 200 },
      ),
    )
    const reader = { read: api.read, post: (path, body) => api.write(path, body, 'csrf', 'scope') }
    if (status !== 503)
      await assert.rejects(
        () => batch5.loadGoals(reader),
        (failure) => failure.status === status,
      )
    else {
      const result = await batch5.loadGoals(reader)
      assert.equal(result.goals[0].current_amount, 86000)
      assert.deepEqual(result.projections, {})
      assert.match(result.projectionErrors[0], /^教育金：/)
    }
  }
})
