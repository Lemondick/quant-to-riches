import type { IndexDefinition, IndexHistory, MarketSnapshot, Quote } from '../services/dataApi'

type Quality = 'valid' | 'stale' | 'unknown_time' | 'invalid' | 'unavailable'
export type IndexCard = { definition: IndexDefinition; quote?: Quote; value: string; change: string; percent: string; tone: 'up' | 'down' | 'neutral'; quality: Quality; qualityLabel: string; reason: string; sourceLabel: string }
const labels: Record<Quality, string> = { valid: '有效观测', stale: '最近有效快照', unknown_time: '时间待核验', invalid: '数据校验未通过', unavailable: '暂不可用' }
const reasons: Record<string, string> = { calendar_or_session_unavailable: '交易日历或时段尚未核验', invalid_source_time: '来源时间异常', index_refresh_failed: '更新失败，保留原始观测', quote_not_returned: '来源未返回该指数', source_unavailable: '公开来源暂不可用', mapping_not_verified: '供应商标识尚未核验', index_identity_or_value_mismatch: '证券身份或数值不一致', invalid_provider_payload: '来源响应格式异常' }
const quality = (value?: string): Quality => value && Object.hasOwn(labels, value) ? value as Quality : 'unknown_time'
const severity: Record<Quality, number> = { valid: 0, stale: 1, unknown_time: 2, unavailable: 3, invalid: 4 }
function numeric(value: unknown): number | undefined {
  if (typeof value !== 'string' && typeof value !== 'number' || String(value).trim() === '') return undefined
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
}
function sourceLabel(value: string | null | undefined, zone: string) {
  const unknown = '来源时间未知'
  if (!value || !/^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)$/.test(value)) return unknown
  if (/[+-]14:(?!00)/.test(value)) return unknown
  const instant = new Date(value), day = new Date(value.slice(0, 10) + 'T00:00:00Z')
  if (!Number.isFinite(instant.getTime()) || !Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== value.slice(0, 10)) return unknown
  try {
    const parts = new Intl.DateTimeFormat('sv-SE', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(instant)
    return parts + ' · ' + zone
  } catch { return unknown }
}
export function indexCards(snapshot?: Pick<MarketSnapshot, 'indices' | 'index_registry' | 'index_statuses'> | null): IndexCard[] {
  return [...snapshot?.index_registry ?? []].filter(row => row.enabled).sort((a, b) => a.display_order - b.display_order).map(definition => {
    const matches = (snapshot?.indices ?? []).filter(row => row.id === definition.id)
    const status = snapshot?.index_statuses?.find(row => row.id === definition.id)
    let quote = matches.length === 1 ? matches[0] : undefined
    let state = quality(status?.quality_status ?? quote?.quality_status)
    if (quote?.quality_status && severity[quality(quote.quality_status)] > severity[state]) state = quality(quote.quality_status)
    let reason = status?.quality_reason ?? quote?.quality_reason ?? ''
    const price = numeric(quote?.price), change = numeric(quote?.change), rate = numeric(quote?.change_rate)
    if (matches.length > 1 || quote && (quote.symbol !== definition.exchange_code || quote.name !== definition.name || quote.asset_type !== 'index' || quote.market !== definition.market || quote.currency !== definition.currency || price === undefined || price <= 0 || change === undefined || rate === undefined)) {
      state = 'invalid'; reason = 'index_identity_or_value_mismatch'; quote = undefined
    } else if (!quote) { if (state !== 'invalid') state = 'unavailable'; reason ||= 'quote_not_returned' }
    const clock = sourceLabel(quote?.source_time, definition.timezone)
    if (quote && state !== 'invalid' && state !== 'unavailable') {
      if (quote.cache_fallback || quote.is_stale || state === 'stale') {
        if (state !== 'unknown_time') state = 'stale'
        reason ||= quote.cache_fallback ? 'index_refresh_failed' : ''
      }
      if (clock === '来源时间未知') state = 'unknown_time'
    }
    if (state === 'invalid' || state === 'unavailable') quote = undefined
    const signed = (value: number) => (value > 0 ? '+' : '') + value.toFixed(2)
    return { definition, quote, quality: state, qualityLabel: labels[state], reason: reasons[reason] ?? (state === 'unknown_time' ? '来源时间或交易时段尚未核验' : reason), sourceLabel: quote ? clock : '来源时间未知', value: quote ? price!.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—', change: quote ? signed(change!) : '—', percent: quote ? signed(rate!) + '%' : '—', tone: quote && rate! > 0 ? 'up' : quote && rate! < 0 ? 'down' : 'neutral' }
  })
}
export function indexHistoryResponse(value: unknown, requestedId: string): IndexHistory {
  if (!value || typeof value !== 'object' || !('id' in value) || value.id !== requestedId) throw new Error('指数历史标识与当前选择不一致。')
  const data = value as IndexHistory
  if (data.quality_status !== undefined && !['valid', 'stale', 'unknown_time', 'unavailable'].includes(data.quality_status)) throw new Error('指数历史数据未通过质量校验。')
  if (!Array.isArray(data.items) || data.quality_status === 'unavailable' && data.items.length) throw new Error('指数历史数据格式无效。')
  return data
}
