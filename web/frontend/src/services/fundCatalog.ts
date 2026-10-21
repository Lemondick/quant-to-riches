export type CatalogFilters = Record<string, string[]>
export type CatalogFund = {
  symbol: string; name: string; fund_type: string | null; asset_class: string | null
  investment_scope: string[]; strategy_tags: string[]; product_structure: string | null
  benchmark_index_id: string | null; benchmark_text: string | null; benchmark_relation: string | null
  share_class: string | null; classification_status: string; classification_is_stale: boolean
  classification_evidence: { field: string; value: string; source: string; source_field: string; source_text: string; rule: string; fetched_at: string | null }[]
  unit_nav: string | null; nav_date: string | null; daily_return: string | null
  nav_source: string | null; nav_fetched_at: string | null; nav_quality_status: string; nav_reason: string | null
  data_source: string; source_time: string | null; fetched_at: string | null; quality_status: string
}
export type CatalogResult = { items: CatalogFund[]; total: number; page: number; page_size: number; directory_count: number; data_source: string; source_time: string | null; fetched_at: string; cache_read_at: string; quality_status: string; stale: boolean; classification_note: string }
export type CatalogCategories = { dimensions: Record<string, { value: string; label: string; count: number }[]> }

export function catalogPath(query: string, page: number, filters: CatalogFilters) {
  const params = new URLSearchParams({ q: query.trim(), page: String(page), page_size: '50' })
  for (const key of Object.keys(filters).sort())
    for (const value of [...new Set(filters[key])].sort()) params.append(key, value)
  return '/funds/catalog?' + params.toString()
}
export function validateCatalog(value: CatalogResult, path: string) {
  const params = new URLSearchParams(path.split('?')[1])
  if (!Array.isArray(value.items) || value.items.length > 50 || !Number.isSafeInteger(value.total) || value.total < value.items.length
    || value.page !== Number(params.get('page')) || value.page_size !== 50 || !['valid', 'stale', 'unknown_time'].includes(value.quality_status)
    || value.items.some(row => !/^[0-9]{6}$/.test(row.symbol) || typeof row.name !== 'string' || !Array.isArray(row.strategy_tags) || !Array.isArray(row.investment_scope)))
    throw new Error('基金目录响应未通过校验，请刷新重试。')
  return value
}
export function catalogPercent(value: string | null) {
  return value == null || value.trim() === '' || !Number.isFinite(Number(value)) ? '—' : (Number(value) > 0 ? '+' : '') + Number(value).toFixed(2) + '%'
}
export function catalogStamp(value?: string | null) {
  const date = value ? new Date(value) : null
  return date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(date) + ' 北京时间' : '来源未提供'
}
