export type ChartLegend = { name: string; value: string; color: string }
export const rangeFactors: Record<string, number> = { '分时': 1, '近5日': 1.6, '近1月': .45, '近3月': 1, '近6月': 1.65, '全部': 2.2 }

export function makeDemoChart(legends: ChartLegend[], range: string) {
  const intraday = range === '分时'
  const count = intraday ? 49 : 32
  const days = range === '近5日' ? 5 : range === '近1月' ? 30 : range === '近6月' ? 180 : range === '全部' ? 365 : 90
  const labels = Array.from({ length: count }, (_, index) => {
    if (intraday) {
      const minutes = index <= 24 ? 570 + index * 5 : 780 + (index - 24) * 5
      return String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(minutes % 60).padStart(2, '0')
    }
    return new Date(Date.UTC(2026, 9, 16) - Math.round((1 - index / (count - 1)) * days) * 86400000).toISOString().slice(5, 10)
  })
  const series = legends.map((legend, seriesIndex) => {
    const raw = Number.parseFloat(legend.value.replace(/[^\d.+-]/g, ''))
    const target = (Number.isFinite(raw) ? raw : 0) * (rangeFactors[range] ?? 1)
    const values = Array.from({ length: count }, (_, index) => {
      const progress = index / (count - 1)
      const fluctuation = Math.sin(index * .63 + seriesIndex) * Math.sin(Math.PI * progress) * (Math.abs(target) * .18 + .12)
      return +(target * progress + fluctuation).toFixed(3)
    })
    return { ...legend, value: (target > 0 ? '+' : '') + target.toFixed(2) + '%', values }
  })
  return { labels, series }
}
