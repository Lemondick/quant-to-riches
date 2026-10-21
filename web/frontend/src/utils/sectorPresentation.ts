import type { MarketSector, SectorCatalog } from '../services/dataApi'
import { updateTime } from './dataQuality.ts'

export const sectorIdentity = (row: Partial<MarketSector>) => `${row.source ?? 'unknown'}:${row.category ?? 'industry'}:${row.code ?? ''}`
const invalid = (row: Partial<MarketSector>) => !!row.quality_status && !['valid', 'stale', 'unknown_time', 'unavailable'].includes(row.quality_status)
export function sectorAmount(value: unknown) {
  return !['string', 'number'].includes(typeof value) || String(value).trim() === '' || !Number.isFinite(Number(value)) ? null : Number(value)
}
export function sectorValue(row: Partial<MarketSector>) {
  return invalid(row) || row.quality_status === 'unavailable' ? null : sectorAmount(row.change_rate)
}
export function sectorTone(row: Partial<MarketSector>) {
  const value = sectorValue(row)
  return value == null || value === 0 ? '' : value > 0 ? 'up' : 'down'
}
export function sectorSourceTime(row: Partial<MarketSector>) {
  if (invalid(row)) return null
  const value = row.source_time ?? row.observed_at
  return value && updateTime(value) !== '更新时间：未知' ? value : null
}
export function sectorTimeLabel(row: Partial<MarketSector>) {
  const value = sectorSourceTime(row)
  return value ? updateTime(value).replace('更新时间：', '') + ' 北京时间' : row.quality_status === 'invalid' ? '时间未通过校验' : '来源未提供'
}
export function sectorQuality(row: Partial<MarketSector>) {
  if (invalid(row)) return '数据校验失败'
  if (sectorValue(row) == null) return '暂无报价'
  const clock = sectorSourceTime(row)
  if (row.quality_status === 'stale' || row.is_stale) return clock ? '最近有效报价 · 已过期' : '旧报价 · 来源时间未知'
  if (!clock) return '来源时间未知 · 延迟无法确认'
  return row.quality_status === 'unknown_time' ? '时效待核验' : '公开源报价'
}
export function sectorCatalogStatus(catalog: Partial<SectorCatalog> | undefined, session: string, now = Date.now()) {
  if (!catalog?.received) return catalog?.http_ok === true && catalog.parse_ok === false ? '响应解析失败 · 暂无可用板块' : '该数据源暂无可用板块目录'
  const parts = [catalog.total == null ? `已接收 ${catalog.received} 项 · 来源总数未知` : `已接收 ${catalog.received} / ${catalog.total} 项`]
  if (catalog.catalog_stale) parts.push('目录刷新失败，保留旧目录')
  if (catalog.quote_payload_complete === true && catalog.quote_total != null) parts.push(`源报价分页已接收 ${catalog.quote_total} 项（非目录覆盖证明）`)
  parts.push(Number.isSafeInteger(catalog.unquoted) && catalog.unquoted! >= 0 ? `${catalog.unquoted} 项暂无报价` : '报价缺失数量未知')
  if (catalog.http_ok === false || catalog.parse_ok === false || catalog.stale && !catalog.catalog_stale) parts.push('报价更新失败，保留最近有效报价')
  const observed = catalog.source_time ?? catalog.observed_at
  if (catalog.source_time_known !== true || !observed) parts.push('部分或全部报价时间未知，延迟无法确认')
  else if (catalog.quality_status === 'invalid' || !Number.isFinite(Date.parse(observed))) parts.push('行情时间未通过校验')
  else if (['continuous_trading', 'auction', 'trading'].includes(session) && now - Date.parse(observed) > 90000) parts.push('行情延迟，保留原始时间')
  return parts.join(' · ')
}
