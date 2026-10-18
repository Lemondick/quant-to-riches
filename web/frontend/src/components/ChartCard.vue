<template>
  <div class="panel chart-panel">
    <div class="panel-head">
      <div><h2>{{ title }}</h2><p>{{ subtitle }}<span v-if="updatedAt !== undefined" data-update-time> · {{ updateTime(updatedAt) }}</span></p></div>
      <div class="segmented" role="group" :aria-label="title + '时间范围'"><button v-for="item in ranges" :key="item" :class="{ selected: item === selectedRange }" :aria-pressed="item === selectedRange" @click="selectRange(item)">{{ item }}</button></div>
    </div>
    <div class="legend"><span v-for="item in data.series" :key="item.name"><i :style="{ background: item.color }" aria-hidden="true"></i>{{ item.name }} <b :class="parseFloat(item.value) < 0 ? 'down' : 'up'">{{ item.value }}</b></span></div>
    <div ref="element" class="echart" role="img" :aria-label="title + '，' + selectedRange + (observations ? '已确认走势' : '演示涨跌幅走势')"></div>
    <p v-if="chartError" class="chart-caption" role="status">图表暂时无法加载，请刷新页面重试。</p>
    <div class="chart-caption"><span>{{ observations ? data.labels.length ? '已确认净值记录' : '暂无可用历史记录' : '演示走势 · 基准日 2026-10-16' }}</span><span>相对起点涨跌幅（%）</span></div>
  </div>
</template>
<script setup lang="ts">
import { updateTime } from '../utils/dataQuality'
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { makeDemoChart, type ChartLegend } from '../utils/charts'
import type { LineChartController } from '../utils/lineChart'
import { usePreferences } from '../composables/usePreferences'
const props = withDefaults(defineProps<{ updatedAt?: string | null; title: string; subtitle: string; legends?: ChartLegend[]; observations?: { labels: string[]; series: (ChartLegend & { values: number[] })[] }; ranges?: string[]; range?: string; variant?: 'portfolio' | 'line' | 'market' }>(), { legends: () => [], ranges: () => ['近1月', '近3月', '近6月', '全部'], variant: 'line' })
const emit = defineEmits<{ 'update:range': [value: string] }>()
const internalRange = ref(props.ranges[1] ?? props.ranges[0])
const selectedRange = computed(() => props.range ?? internalRange.value)
const data = computed(() => props.observations ?? makeDemoChart(props.legends, selectedRange.value))
const element = ref<HTMLElement>()
const chartError = ref(false)
const { preferences } = usePreferences()
let chart: LineChartController | undefined
let observer: ResizeObserver | undefined
let disposed = false
function selectRange(value: string) { internalRange.value = value; emit('update:range', value) }
function render() {
  if (!chart) return
  chart.render(data.value, !preferences.value.reducedMotion && !matchMedia('(prefers-reduced-motion: reduce)').matches)
}
onMounted(async () => {
  if (!element.value) return
  try {
    const { createLineChart } = await import('../utils/lineChart')
    if (disposed || !element.value) return
    chart = createLineChart(element.value)
    render()
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(element.value)
  } catch { if (!disposed) chartError.value = true }
})
watch(data, render, { deep: true })
onBeforeUnmount(() => { disposed = true; observer?.disconnect(); chart?.dispose() })
</script>
<style scoped>
.legend{min-height:1.7em}
</style>
