import { readFileSync } from 'node:fs'

export const indexRegistry = JSON.parse(
  readFileSync(new URL('../../backend/src/quant_assistant/providers/index_registry.json', import.meta.url), 'utf8'),
).definitions
const dates = '2026-10-16T15:00:00+08:00'
const position = {
  symbol: '510300',
  name: '布局验收基金',
  asset_type: 'fund',
  quantity: '100.00000000',
  average_cost: '1.00',
  market_value: '120.00',
  as_of_date: '2026-10-16',
}
const market = {
  registry_version: 'explicit-layout-fixture-v1',
  index_registry: indexRegistry,
  index_statuses: indexRegistry.map((row) => ({
    id: row.id,
    quality_status: 'valid',
    quality_reason: null,
    source: 'explicit-local-layout-fixture',
    source_time: dates,
    fetched_at: dates,
    cache_read_at: dates,
  })),
  indices: indexRegistry.map((row, i) => ({
    id: row.id,
    symbol: row.exchange_code,
    name: row.name,
    market: row.market,
    currency: row.currency,
    asset_type: 'index',
    price: String(3000 + i * 100),
    change: String((i % 3) - 1),
    change_rate: String((i % 3) - 1),
    observed_at: dates,
    source_time: dates,
    fetched_at: dates,
    quality_status: 'valid',
    source: 'explicit-local-layout-fixture',
  })),
  fund_rankings: [
    { symbol: '510300', name: '布局验收基金', unit_nav: '1.20', change_rate: '0.50', nav_date: '2026-10-16' },
  ],
  sector_rankings: ['银行', '证券', '半导体', '医药', '电力', '汽车'].map((name, i) => ({
    code: String(i),
    name,
    change_rate: String(i / 10),
  })),
  observed_at: dates,
  stale: false,
  unavailable: [],
}
const summary = {
  user_id: 'layout-only',
  generated_at: dates,
  unavailable: [],
  portfolio: { positions: [position], cash: '1000.00', total_value: '1120.00', as_of_date: '2026-10-16' },
  preferences: { watchlist: [{ symbol: '510300', name: '布局验收基金', asset_type: 'fund' }] },
  valuations: {
    items: [
      {
        symbol: '510300',
        estimate_nav: '1.20',
        change_rate: '0.5',
        observed_at: dates,
        source: 'explicit-local-layout-fixture',
        nav_date: '2026-10-16',
        valuation_type: 'latest_nav',
      },
    ],
    unavailable: [],
  },
  quotes: { items: [], unavailable: [] },
  market,
}
export function responseFor(url) {
  const route = url.pathname.replace('/api/v1', '')
  if (route === '/auth/session')
    return {
      user: { id: 'layout-only', username: '布局验收账户', role: 'admin' },
      csrf_token: 'layout-only-not-valid-on-any-server',
      cache_scope: 'layout-only',
    }
  if (route === '/dashboard/summary') return summary
  if (route === '/portfolio/history')
    return {
      points: ['2026-10-14', '2026-10-15', '2026-10-16'].map((as_of_date, i) => ({
        as_of_date,
        unit_nav: String(1 + i / 100),
        total_value: '1120',
        cash: '1000',
        net_external_flow: '0',
      })),
    }
  if (route === '/market/snapshot') return market
  if (route === '/market/history') {
    const definition = indexRegistry.find(
      (row) =>
        row.id === url.searchParams.get('index_id') ||
        (row.legacy_history_symbol && row.legacy_history_symbol === url.searchParams.get('symbol')),
    )
    if (!definition?.history)
      return {
        id: definition?.id,
        symbol: definition?.exchange_code,
        items: [],
        quality_status: 'unavailable',
        quality_reason: 'history_not_connected',
      }
    return {
      id: definition.id,
      symbol: definition.exchange_code,
      name: '布局验收指数',
      source: 'explicit-local-layout-fixture',
      observed_at: dates,
      stale: false,
      unavailable: [],
      items: ['2026-10-14', '2026-10-15', '2026-10-16'].map((date, i) => ({
        date,
        open: String(3000 + i),
        high: String(3010 + i),
        low: String(2990 + i),
        close: String(3005 + i),
        volume: '100',
      })),
    }
  }
  if (route === '/market/news')
    return {
      items: [
        {
          id: 'layout-article',
          title: '布局验收资讯标题',
          summary: '明确的本地布局夹具，不发布为真实市场资讯。',
          source: '布局验收',
          published_at: dates,
        },
      ],
      stale: false,
      unavailable: false,
    }
  if (route === '/ledger') return { entries: [], total_fees: '0', total_dividends: '0' }
  if (route === '/preferences') return summary.preferences
  if (route === '/auth/sessions')
    return {
      items: [{ session_id: 'layout-only', device: '布局验收', created_at: dates, expires_at: dates, current: true }],
    }
  if (route === '/admin/users')
    return {
      items: [
        {
          id: 'layout-only',
          username: '布局验收账户',
          role: 'admin',
          status: 'active',
          requests_7d: 0,
          last_login_at: dates,
        },
      ],
      registered: 1,
      limit: 30,
      registration_enabled: false,
    }
  if (route === '/research/strategy-settings')
    return {
      parameters: {
        top_candidate_count: 10,
        max_names: 5,
        target_weight: 0.06,
        exit_percentile: 0.2,
        stop_loss_pct: null,
        min_amount_20: 50000000,
      },
    }
  if (route === '/research/previews' || route === '/research/agent-runs') return { items: [] }
  if (route === '/advisor/model-settings')
    return {
      configured: false,
      active: false,
      revision: 0,
      models: { news: '', strategy: '', reviewer: '', decision: '' },
      provider: { endpoint: '', revision: 0, has_key: false, models: [] },
      jev: { revision: 0, has_key: false, models: [] },
    }
  throw new Error('Unexpected layout fixture read: ' + route)
}
