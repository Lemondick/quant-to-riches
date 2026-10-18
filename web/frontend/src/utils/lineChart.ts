import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ChartLegend } from './charts'

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])
type ChartData = { labels: string[]; series: (ChartLegend & { values: number[] })[] }
export type LineChartController = ReturnType<typeof createLineChart>

// Only the drawing engine is asynchronous; its card and plot space already exist.
export function createLineChart(element: HTMLElement) {
  const chart = echarts.init(element)
  return {
    render(data: ChartData, animation: boolean) {
      chart.setOption({
        animation, animationDuration: 0, animationDurationUpdate: 150,
        grid: { left: 10, right: 18, top: 16, bottom: 26, containLabel: true },
        xAxis: { type: 'category', data: data.labels, boundaryGap: false, axisLabel: { color: '#91a7c5', fontSize: 10, hideOverlap: true, formatter: (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.slice(5) : value }, axisLine: { lineStyle: { color: '#29415e' } }, axisTick: { show: false } },
        yAxis: { type: 'value', axisLabel: { color: '#91a7c5', fontSize: 10, formatter: '{value}%' }, splitLine: { lineStyle: { color: '#223752', type: 'dashed' } } },
        tooltip: { trigger: 'axis', backgroundColor: '#112138', borderColor: '#3b5e88', textStyle: { color: '#e5eefc', fontSize: 12 }, valueFormatter: (value: number) => value.toFixed(2) + '%' },
        series: data.series.map(item => ({ name: item.name, type: 'line', data: item.values, smooth: .2, symbol: 'none', lineStyle: { width: 2.3, color: item.color }, itemStyle: { color: item.color }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: item.color + '20' }, { offset: 1, color: item.color + '00' }]) } }))
      }, { notMerge: true })
    },
    resize: () => chart.resize(),
    dispose: () => chart.dispose(),
  }
}
