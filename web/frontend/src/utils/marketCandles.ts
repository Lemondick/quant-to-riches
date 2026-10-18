export type Candle = { date: string; open: number; high: number; low: number; close: number; volume?: number }
export type CandlePeriod = 'day' | 'week' | 'month' | 'year'
function checkedDay(day: string) {
  const value = new Date(day + 'T00:00:00Z')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(value.getTime()) || value.toISOString().slice(0, 10) !== day) throw new Error('历史数据日期无效。')
  return value
}
function key(day: string, period: CandlePeriod) {
  const value = checkedDay(day)
  if (period === 'year') return day.slice(0, 4) + '-01-01'
  if (period === 'month') return day.slice(0, 7) + '-01'
  if (period === 'week') value.setUTCDate(value.getUTCDate() - (value.getUTCDay() + 6) % 7)
  return value.toISOString().slice(0, 10)
}
export function navCandles(points: { nav_date: string; unit_nav: unknown }[]): Candle[] {
  return points.map(point => {
    const value = Number(point.unit_nav)
    if (!Number.isFinite(value) || value <= 0) throw new Error('净值数据无效。')
    return { date: point.nav_date, open: value, high: value, low: value, close: value }
  })
}
export function aggregateCandles(points: Candle[], period: CandlePeriod): Candle[] {
  if (!['day', 'week', 'month', 'year'].includes(period) || points.length > 20000) throw new Error('K线周期或历史条数无效。')
  const seen = new Set<string>(), groups = new Map<string, Candle>()
  for (const point of [...points].sort((a, b) => a.date.localeCompare(b.date))) {
    const date = key(point.date, period)
    const { open, high, low, close, volume } = point
    if (seen.has(point.date) || ![open, high, low, close].every(value => Number.isFinite(value) && value > 0) || high < Math.max(open, close) || low > Math.min(open, close) || volume !== undefined && (!Number.isFinite(volume) || volume < 0)) throw new Error('K线历史数据无效。')
    seen.add(point.date)
    const previous = groups.get(date)
    if (!previous) groups.set(date, { ...point, date })
    else { previous.high = Math.max(previous.high, high); previous.low = Math.min(previous.low, low); previous.close = close; if (volume !== undefined) previous.volume = (previous.volume ?? 0) + volume }
  }
  return [...groups.values()]
}
