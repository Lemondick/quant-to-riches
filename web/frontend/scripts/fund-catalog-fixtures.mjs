// Explicit local browser fixtures only. Never imported by application/provider code.
export const catalogFunds = Array.from({ length: 123 }, (_, i) => ({
  symbol: String(100000 + i),
  name: `明确验收基金${i}`,
  fund_type: i === 0 ? '货币型-普通货币' : '指数型-股票',
  asset_class: i === 0 ? '货币型' : i === 2 ? null : '股票型',
  investment_scope: [],
  strategy_tags: i > 2 ? ['指数策略'] : [],
  product_structure: null,
  benchmark_index_id: null,
  benchmark_index_ids: [],
  share_class: null,
  classification_status: i === 2 ? 'unknown' : 'partial',
  classification_is_stale: false,
  classification_evidence: [],
  unit_nav: i === 1 ? '1.2300' : null,
  nav_date: i === 1 ? '2026-10-19' : null,
  daily_return: i === 1 ? '0' : null,
  nav_source: i === 1 ? 'eastmoney' : null,
  nav_fetched_at: i === 1 ? '2026-10-20T08:00:00Z' : null,
  nav_quality_status: i === 1 ? 'valid' : 'unavailable',
  nav_reason: i === 0 ? 'money_fund_metric_not_verified' : null,
  data_source: 'eastmoney',
  source_time: null,
  fetched_at: '2026-10-20T08:00:00Z',
  quality_status: 'unknown_time',
}))
export const catalogDimensions = Object.fromEntries(
  [
    'asset_class',
    'fund_type',
    'investment_scope',
    'strategy_tags',
    'product_structure',
    'benchmark_index_id',
    'share_class',
    'regulatory_category',
    'classification_status',
  ].map((key) => {
    const values = new Map()
    for (const row of catalogFunds) {
      const raw = row[key],
        options = Array.isArray(raw) ? raw : raw ? [raw] : []
      for (const value of options.length ? options : ['__unknown']) values.set(value, (values.get(value) ?? 0) + 1)
    }
    return [
      key,
      [...values].map(([value, count]) => ({ value, label: value === '__unknown' ? '未分类 / 待核验' : value, count })),
    ]
  }),
)
export function catalogResponse(url, mode = 'normal') {
  const p = url.searchParams,
    query = (p.get('q') ?? '').trim(),
    page = Number(p.get('page') ?? 1)
  const selected = catalogFunds.filter(
    (row) =>
      (row.name + row.symbol).includes(query) &&
      Object.keys(catalogDimensions).every((key) => {
        const expected = p.getAll(key),
          actual = Array.isArray(row[key]) ? row[key] : row[key] ? [row[key]] : []
        const values = actual.length ? actual : ['__unknown']
        return (
          !expected.length ||
          (key === 'strategy_tags'
            ? expected.every((v) => values.includes(v))
            : expected.some((v) => values.includes(v)))
        )
      }),
  )
  return {
    items: mode === 'empty' ? [] : structuredClone(selected.slice((page - 1) * 50, page * 50)),
    total: mode === 'empty' ? 0 : selected.length,
    page,
    page_size: 50,
    directory_count: catalogFunds.length,
    data_source: 'eastmoney',
    source_time: null,
    fetched_at: '2026-10-20T08:00:00Z',
    cache_read_at: '2026-10-20T09:00:00Z',
    quality_status: mode === 'stale' ? 'stale' : 'unknown_time',
    stale: mode === 'stale',
    classification_note: '明确本地验收夹具，不代表真实市场数据。',
  }
}
