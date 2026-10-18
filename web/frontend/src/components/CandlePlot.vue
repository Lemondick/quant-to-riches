<template>
  <div class="kline-terminal">
    <div class="terminal-toolbar" role="group" aria-label="K线查看工具">
      <div class="terminal-indicators">
        <button
          role="button"
          tabindex="0"
          :aria-pressed="showMA"
          @click="toggleMA"
          @keydown.enter.prevent="toggleMA"
          @keydown.space.prevent="toggleMA"
        >
          均线 MA
        </button>
        <button
          role="button"
          tabindex="0"
          :aria-pressed="showMACD"
          @click="toggleMACD"
          @keydown.enter.prevent="toggleMACD"
          @keydown.space.prevent="toggleMACD"
        >
          MACD
        </button>
      </div>
      <div class="terminal-navigation">
        <button
          role="button"
          tabindex="0"
          aria-label="放大K线"
          @click="zoom(2)"
          @keydown.enter.prevent="zoom(2)"
          @keydown.space.prevent="zoom(2)"
        >
          ＋
        </button>
        <button
          role="button"
          tabindex="0"
          aria-label="缩小K线"
          @click="zoom(-2)"
          @keydown.enter.prevent="zoom(-2)"
          @keydown.space.prevent="zoom(-2)"
        >
          －
        </button>
        <button
          role="button"
          tabindex="0"
          aria-label="更早历史"
          @click="pan(1)"
          @keydown.enter.prevent="pan(1)"
          @keydown.space.prevent="pan(1)"
        >
          ←
        </button>
        <button
          role="button"
          tabindex="0"
          aria-label="较新历史"
          @click="pan(-1)"
          @keydown.enter.prevent="pan(-1)"
          @keydown.space.prevent="pan(-1)"
        >
          →
        </button>
        <button
          role="button"
          tabindex="0"
          @click="latest"
          @keydown.enter.prevent="latest"
          @keydown.space.prevent="latest"
        >
          最新
        </button>
      </div>
    </div>
    <div v-if="inspected" class="terminal-readout" aria-live="off">
      <span class="terminal-date"
        >{{ inspected.date }}</span>
      <span v-for="item in values" :key="item.label"
        >{{ item.label }} <strong :class="direction">{{ item.value }}</strong></span>
    </div>
    <div v-if="error" class="candle-error" role="alert">{{ error }}</div>
    <div
      v-else
      ref="host"
      class="terminal-canvas"
      :data-period="period"
      :data-visible-from="visibleFrom"
      :data-visible-to="visibleTo"
      :data-inspected-index="selected"
      role="img"
      tabindex="0"
      :aria-label="`${seriesLabel}${periodLabel}，含MA均线和MACD；左右键查看，Home和End到首末观测，加减键缩放`"
      @keydown="keyboard"
    />
    <div class="terminal-footer">
      <span>{{ visibleStart }} — {{ visibleEnd }} · {{ visibleCount }} / {{ candles.length }} 根{{ periodLabel }}</span>
      <span>滚轮缩放 · 拖动查看 · 方向键检查</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, dispose } from 'klinecharts'
import type { Chart, Crosshair, KLineData, PeriodType } from 'klinecharts'
import type { Candle } from '../utils/marketCandles'

const props = defineProps<{ candles: Candle[]; period: string; periodLabel: string; seriesLabel: string; precision: number }>()
const host = ref<HTMLElement | null>(null)
const selected = ref<number | null>(null)
const visibleFrom = ref(0)
const visibleTo = ref(0)
const showMA = ref(true)
const showMACD = ref(true)
const error = ref('')
let chart: Chart | null = null
let resizeObserver: ResizeObserver | undefined
let themeObserver: MutationObserver | undefined
let macdPane: string | null = null
let resizeFrame = 0
let loadedBars: KLineData[] = []
const bars = computed<KLineData[]>(() =>
  props.candles.map((candle) => ({
    timestamp: Date.parse(`${candle.date}T00:00:00+08:00`),
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  })),
)
const inspectedIndex = computed(() => selected.value ?? Math.max(0, visibleTo.value - 1))
const inspected = computed(() => props.candles[inspectedIndex.value])
const direction = computed(() => {
  const item = inspected.value
  if (!item) return ''
  const reference = props.period === 'day' ? (props.candles[inspectedIndex.value - 1]?.close ?? item.open) : item.open
  return item.close > reference ? 'rising' : item.close < reference ? 'falling' : ''
})
const values = computed(() =>
  inspected.value
    ? [
        { label: '开', value: inspected.value.open.toFixed(props.precision) },
        { label: '高', value: inspected.value.high.toFixed(props.precision) },
        { label: '低', value: inspected.value.low.toFixed(props.precision) },
        { label: '收', value: inspected.value.close.toFixed(props.precision) },
      ]
    : [],
)
const visibleStart = computed(() => props.candles[visibleFrom.value]?.date || '—')
const visibleEnd = computed(() => props.candles[Math.max(0, visibleTo.value - 1)]?.date || '—')
const visibleCount = computed(() => Math.max(0, visibleTo.value - visibleFrom.value))
function syncRange() {
  if (!chart) return
  const range = chart.getVisibleRange()
  visibleFrom.value = Math.max(0, Math.min(props.candles.length, Math.floor(range.from)))
  visibleTo.value = Math.max(visibleFrom.value, Math.min(props.candles.length, Math.ceil(range.to)))
}
function crosshair(value: unknown) {
  const data = value as Crosshair
  if (!data) {
    selected.value = null
    return
  }
  const index = data.dataIndex
  selected.value = index != null && index >= 0 && index < props.candles.length ? index : null
}
function styles() {
  if (!chart || !host.value) return
  const dark = document.documentElement.dataset.appearance !== 'light'
  const css = getComputedStyle(host.value)
  const color = (name: string) => css.getPropertyValue(name).trim()
  const up = color('--red'),
    down = color('--green'),
    neutral = color('--text-2')
  chart.setStyles(dark ? 'dark' : 'light')
  chart.setStyles({
    grid: {
      horizontal: { color: color('--border'), style: 'dashed', dashedValue: [3, 3] },
      vertical: { color: color('--border'), style: 'dashed', dashedValue: [3, 3] },
    },
    candle: {
      type: 'candle_up_stroke',
      bar: {
        compareRule: props.period === 'day' ? 'previous_close' : 'current_open',
        upColor: up,
        downColor: down,
        noChangeColor: neutral,
        upBorderColor: up,
        downBorderColor: down,
        noChangeBorderColor: neutral,
        upWickColor: up,
        downWickColor: down,
        noChangeWickColor: neutral,
      },
      priceMark: {
        high: { color: neutral },
        low: { color: neutral },
        last: { upColor: up, downColor: down, noChangeColor: neutral, text: { size: 11 }, line: { size: 1 } },
      },
      tooltip: { showRule: 'none' },
    },
    indicator: {
      bars: [{ upColor: up, downColor: down, noChangeColor: neutral }],
      lines: [
        { color: dark ? '#f6cc58' : '#af7400', size: 1 },
        { color: dark ? '#bf98ff' : '#9156db', size: 1 },
        { color: dark ? '#79a9ff' : '#3474d4', size: 1 },
        { color: dark ? '#49d3c7' : '#168d82', size: 1 },
      ],
      tooltip: { title: { color: neutral, size: 11 }, legend: { size: 11 } },
    },
    xAxis: {
      axisLine: { color: color('--border') },
      tickText: { color: neutral, size: 11 },
      tickLine: { show: false },
    },
    yAxis: { axisLine: { show: false }, tickText: { color: neutral, size: 11 }, tickLine: { show: false } },
    separator: { color: color('--border'), size: 1, activeBackgroundColor: color('--surface') },
    crosshair: {
      horizontal: {
        line: { color: neutral, style: 'dashed', dashedValue: [4, 3] },
        text: { backgroundColor: color('--text-1'), color: color('--surface'), size: 11 },
      },
      vertical: {
        line: { color: neutral, style: 'dashed', dashedValue: [4, 3] },
        text: { backgroundColor: color('--text-1'), color: color('--surface'), size: 11 },
      },
    },
  })
}
function fitMACD() {
  if (chart && macdPane)
    chart.setPaneOptions({ id: macdPane, height: (host.value?.clientWidth || 0) < 500 ? 94 : 116, minHeight: 74 })
}
function toggleMA() {
  if (!chart) return
  showMA.value = !showMA.value
  if (showMA.value)
    chart.createIndicator({ name: 'MA', paneId: 'candle_pane', calcParams: [5, 10, 20, 30], precision: props.precision })
  else chart.removeIndicator({ name: 'MA', paneId: 'candle_pane' })
}
function toggleMACD() {
  if (!chart) return
  showMACD.value = !showMACD.value
  if (showMACD.value) {
    macdPane = chart.createIndicator({ name: 'MACD', calcParams: [12, 26, 9], precision: props.precision })
    fitMACD()
  } else {
    chart.removeIndicator({ name: 'MACD' })
    macdPane = null
  }
}
function zoom(amount: number) {
  chart?.zoomAtDataIndex(amount, inspectedIndex.value)
  syncRange()
}
function pan(direction: number) {
  chart?.scrollByDistance(direction * (host.value?.clientWidth || 300) * 0.6)
  selected.value = null
  syncRange()
}
function latest() {
  chart?.scrollToRealTime()
  selected.value = null
  syncRange()
}
function keyboard(event: KeyboardEvent) {
  if (
    !chart ||
    !props.candles.length ||
    !['ArrowLeft', 'ArrowRight', 'Home', 'End', '+', '=', '-', 'Escape'].includes(event.key)
  )
    return
  event.preventDefault()
  if (['+', '=', '-'].includes(event.key)) {
    zoom(event.key === '-' ? -2 : 2)
    return
  }
  if (event.key === 'Escape') {
    selected.value = null
    chart.executeAction('onCrosshairChange', { paneId: 'candle_pane' })
    return
  }
  const index =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? props.candles.length - 1
        : Math.max(0, Math.min(props.candles.length - 1, inspectedIndex.value + (event.key === 'ArrowLeft' ? -1 : 1)))
  if (index < visibleFrom.value || index >= visibleTo.value) chart.scrollToDataIndex(index)
  const point = chart.convertToPixel(
    { dataIndex: index, value: props.candles[index].close },
    { paneId: 'candle_pane' },
  ) as { x: number; y: number }
  chart.executeAction('onCrosshairChange', { paneId: 'candle_pane', x: point.x, y: point.y })
  selected.value = index
  syncRange()
}
function reloadData() {
  if (!chart) return
  if (bars.value.some((bar) => !Number.isFinite(bar.high * 32))) {
    error.value = '历史数值超出可绘制范围，请核对数据来源。'
    return
  }
  const index = inspectedIndex.value
  const previous = loadedBars[index]?.timestamp
  const wasOlder = visibleTo.value < loadedBars.length
  const previousRight = loadedBars[Math.max(0, visibleTo.value - 1)]?.timestamp
  loadedBars = bars.value
  chart.resetData()
  if (wasOlder && previousRight) {
    const right = loadedBars.findIndex((bar) => bar.timestamp === previousRight)
    if (right >= 0) chart.scrollToDataIndex(right)
  }
  selected.value = previous ? loadedBars.findIndex((bar) => bar.timestamp === previous) : null
  if (selected.value === -1) selected.value = null
  syncRange()
}
onMounted(async () => {
  await nextTick()
  if (!host.value) return
  chart = init(host.value, { locale: 'zh-CN', timezone: 'Asia/Shanghai', hotkey: { enabled: false } })
  if (!chart) {
    error.value = '图表暂时无法初始化，请刷新页面。'
    return
  }
  chart.setDataLoader({
    getBars: ({ type, callback }) => {
      callback(type === 'init' ? bars.value : [], false)
      syncRange()
    },
  })
  chart.setSymbol({ ticker: props.seriesLabel, pricePrecision: props.precision, volumePrecision: 0 })
  chart.setOffsetRightDistance(24)
  chart.setBarSpace(Math.max(6, Math.min(14, (host.value.clientWidth - 66) / 90)))
  chart.setPeriod({ span: 1, type: props.period as PeriodType })
  chart.createIndicator({ name: 'MA', paneId: 'candle_pane', calcParams: [5, 10, 20, 30], precision: props.precision })
  macdPane = chart.createIndicator({ name: 'MACD', calcParams: [12, 26, 9], precision: props.precision })
  fitMACD()
  styles()
  chart.subscribeAction('onCrosshairChange', crosshair)
  chart.subscribeAction('onVisibleRangeChange', syncRange)
  loadedBars = bars.value
  syncRange()
  resizeObserver = new ResizeObserver(() => {
    cancelAnimationFrame(resizeFrame)
    resizeFrame = requestAnimationFrame(() => {
      chart?.resize()
      fitMACD()
      syncRange()
    })
  })
  resizeObserver.observe(host.value)
  themeObserver = new MutationObserver(styles)
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-appearance', 'data-market-colors'] })
})
watch(bars, reloadData)
watch(
  () => props.period,
  () => {
    selected.value = null
    chart?.setPeriod({ span: 1, type: props.period as PeriodType })
    styles()
    syncRange()
  },
)
onBeforeUnmount(() => {
  cancelAnimationFrame(resizeFrame)
  resizeObserver?.disconnect()
  themeObserver?.disconnect()
  if (chart) dispose(chart)
  chart = null
})
</script>

<style scoped>
.kline-terminal {
  margin-top: 12px;
  min-width: 0;
}
.terminal-toolbar,
.terminal-indicators,
.terminal-navigation,
.terminal-readout,
.terminal-footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.terminal-toolbar {
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.terminal-toolbar button {
  margin: 0;
  padding: 0 10px;
  min-width: 32px;
  line-height: 30px;
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 12px;
  background: var(--surface);
  color: var(--text-2);
}
.terminal-toolbar button[aria-pressed='true'] {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--surface);
}
.terminal-readout {
  min-height: 40px;
  gap: 14px;
  padding: 8px 0;
  font-size: 12px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}
.terminal-readout strong {
  color: var(--text-1);
  font-weight: 500;
}
.terminal-readout strong.rising {
  color: var(--qa-positive);
}
.terminal-readout strong.falling {
  color: var(--qa-negative);
}
.terminal-date {
  color: var(--text-1);
}
.terminal-canvas {
  height: 440px;
  min-width: 0;
  width: 100%;
  background: var(--surface);
  touch-action: none;
}
.terminal-canvas:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.terminal-footer {
  justify-content: space-between;
  border-top: 1px solid var(--border);
  padding-top: 10px;
  font-size: 11px;
  color: var(--text-2);
  line-height: 1.7;
}
.candle-error {
  color: var(--orange);
  padding: 20px 0;
}
@media (max-width: 600px) {
  .terminal-canvas {
    height: 350px;
  }
  .terminal-readout {
    gap: 8px 12px;
  }
  .terminal-date {
    flex-basis: 100%;
  }
  .terminal-toolbar {
    gap: 8px;
  }
}
</style>
