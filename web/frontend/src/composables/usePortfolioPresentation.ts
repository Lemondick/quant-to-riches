import { computed, ref } from 'vue'
import { useDashboardSummary } from './useDashboardSummary'
import { useAccountResource } from './useAccountResource'
import { money, percent } from '../utils/format'
import { usableEstimate } from '../utils/dataQuality'

type Point = { as_of_date: string; unit_nav: string; total_value: string; cash: string; net_external_flow: string }

export function usePortfolioPresentation() {
  const summary = useDashboardSummary()
  const history = useAccountResource('portfolio-history', (api, signal) => api.read<{ points: Point[] }>('/portfolio/history?limit=180', signal))
  const range = ref('近3月')
  const positions = computed(() => summary.data.value?.portfolio.positions.filter(item => item.asset_type !== 'cash') ?? [])
  const total = computed(() => Number(summary.data.value?.portfolio.total_value ?? 0))
  const cost = computed(() => positions.value.reduce((sum, item) => sum + Number(item.quantity) * Number(item.average_cost), 0))
  const profit = computed(() => positions.value.reduce((sum, item) => sum + Number(item.market_value), 0) - cost.value)
  const daily = computed(() => {
    let value = 0
    for (const position of positions.value) {
      const quote = summary.data.value?.valuations.items.find(item => item.symbol === position.symbol)
        ?? summary.data.value?.quotes.items.find(item => item.symbol === position.symbol && item.asset_type === position.asset_type)
      if (!quote) return null
      if ('estimate_nav' in quote && !usableEstimate(quote) || quote.quality_status !== 'valid' || quote.is_stale) return null
      const rate = Number(quote.change_rate) / 100
      if (!Number.isFinite(rate) || rate <= -1) return null
      const price = 'estimate_nav' in quote ? Number(quote.estimate_nav) : Number(quote.price)
      value += Number(position.quantity) * price * rate / (1 + rate)
    }
    return value
  })
  const allocation = computed(() => ['股票', '基金', '现金'].map((name, index) => {
    const value = name === '现金' ? Number(summary.data.value?.portfolio.cash ?? 0)
      : positions.value.filter(item => item.asset_type === (name === '基金' ? 'fund' : 'stock')).reduce((sum, item) => sum + Number(item.market_value), 0)
    return { name, value: money(value), pct: percent(total.value ? value / total.value * 100 : 0, false), color: ['#4d8dff', '#ff9d54', '#42ddb1'][index], weight: value }
  }))
  const observations = computed(() => {
    const points = [...(history.data.value?.points ?? [])].filter(item => Number(item.unit_nav) > 0).sort((a, b) => a.as_of_date.localeCompare(b.as_of_date))
    const count = range.value === '近1月' ? 30 : range.value === '近3月' ? 90 : range.value === '近6月' ? 180 : points.length
    const selected = points.slice(-count), first = Number(selected[0]?.unit_nav)
    const values = selected.map(item => (Number(item.unit_nav) / first - 1) * 100)
    return { labels: selected.map(item => item.as_of_date), series: selected.length ? [{ name: '本组合', color: '#4d8dff', value: percent(values.at(-1) ?? 0), values }] : [] }
  })
  return { ...summary, positions, total, cost, profit, daily, allocation, observations, range, history,
    refresh: async () => { await Promise.all([summary.refresh(), history.refresh()]) } }
}
