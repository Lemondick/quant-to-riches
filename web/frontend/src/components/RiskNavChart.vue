<template>
  <div ref="element" class="echart" role="img" aria-label="组合净值与回撤走势"></div>
  <p v-if="chartError" class="chart-caption" role="status">图表暂时无法加载，请刷新页面重试。</p>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { DrawdownPoint } from '../services/batch3Api'

const props = defineProps<{ points: DrawdownPoint[] }>()
const element = ref<HTMLElement>()
const chartError = ref(false)
let chart: { render: (points: DrawdownPoint[]) => void; resize: () => void; dispose: () => void } | undefined
let observer: ResizeObserver | undefined
let disposed = false

onMounted(async () => {
  if (!element.value) return
  try {
    const echarts = await import('echarts/core')
    const { LineChart } = await import('echarts/charts')
    const { GridComponent, TooltipComponent } = await import('echarts/components')
    const { CanvasRenderer } = await import('echarts/renderers')
    echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])
    if (disposed || !element.value) return
    const instance = echarts.init(element.value)
    chart = {
      render: points => {
        instance.setOption({
          animation: false,
          grid: { left: 12, right: 44, top: 16, bottom: 26, containLabel: true },
          xAxis: {
            type: 'category', data: points.map(p => p.date), boundaryGap: false,
            axisLabel: { color: '#91a7c5', fontSize: 10, hideOverlap: true, formatter: (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.slice(5) : value },
            axisLine: { lineStyle: { color: '#29415e' } }, axisTick: { show: false },
          },
          yAxis: [
            { type: 'value', axisLabel: { color: '#91a7c5', fontSize: 10 }, splitLine: { lineStyle: { color: '#223752', type: 'dashed' } } },
            { type: 'value', max: 0, axisLabel: { color: '#91a7c5', fontSize: 10, formatter: '{value}%' }, splitLine: { show: false } },
          ],
          tooltip: {
            trigger: 'axis', backgroundColor: '#112138', borderColor: '#3b5e88', textStyle: { color: '#e5eefc', fontSize: 12 },
            formatter: (items: { name: string; dataIndex: number }[]) => {
              const point = points[items[0]?.dataIndex ?? 0]
              if (!point) return ''
              return `${point.date}<br/>净值 ${point.nav.toFixed(4)}<br/>回撤 ${point.drawdown_pct.toFixed(2)}%`
            },
          },
          series: [
            {
              name: '净值', type: 'line', yAxisIndex: 0, data: points.map(p => +p.nav.toFixed(4)),
              smooth: 0.2, symbol: 'none', lineStyle: { width: 2.3, color: '#4d8dff' }, itemStyle: { color: '#4d8dff' },
            },
            {
              name: '回撤', type: 'line', yAxisIndex: 1, data: points.map(p => +p.drawdown_pct.toFixed(2)),
              smooth: 0.2, symbol: 'none', lineStyle: { width: 1.4, color: '#ff6b6b' }, itemStyle: { color: '#ff6b6b' },
              areaStyle: { color: 'rgba(255,107,107,0.28)' },
            },
          ],
        }, { notMerge: true })
      },
      resize: () => instance.resize(),
      dispose: () => instance.dispose(),
    }
    chart.render(props.points)
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(element.value)
  } catch { if (!disposed) chartError.value = true }
})
watch(() => props.points, value => chart?.render(value), { deep: true })
onBeforeUnmount(() => { disposed = true; observer?.disconnect(); chart?.dispose() })
</script>
<style scoped>
.echart { min-height: 320px }
</style>
