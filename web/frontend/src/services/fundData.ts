import { apiCache } from './apiCache'
import { createDataApi } from './dataApi'
import { readPreparedPublic } from './preparedPublic'

export type FundDetail = { symbol: string; name: string; fund_type: string | null; company: string | null; observed_at: string; stale: boolean; updated_at?: string | null; nav_updated_at?: string | null; nav_history: { unit_nav: string; nav_date: string }[] }
const base = import.meta.env.VITE_API_BASE_URL ?? '', api = createDataApi(base)
export const fundDataKey = (symbol: string) => `${base}:public:fund:${symbol}`
export async function readFundDetail(symbol: string, signal: AbortSignal) {
  if (!/^\d{6}$/.test(symbol)) throw new Error('基金代码无效。')
  const value = await readPreparedPublic(() => api.read<FundDetail>(`/funds/${symbol}/detail`, signal), signal)
  if (value.symbol !== symbol || !Array.isArray(value.nav_history)) throw new Error('基金资料格式无效。')
  return value
}
const preparing = new Set<string>()
export function prepareFundDetail(symbol: string) {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection
  if (connection?.saveData || /2g/.test(connection?.effectiveType ?? '') || preparing.size >= 2 || preparing.has(symbol)) return
  preparing.add(symbol)
  void apiCache.revalidate(fundDataKey(symbol), signal => readFundDetail(symbol, signal), 300000).catch(() => {}).finally(() => preparing.delete(symbol))
}
