<template>
  <div class="panel candle-card" :data-symbol="symbol" :aria-busy="loading && !points.length">
    <div class="panel-head"><div><h2>{{ title }}</h2><p>{{ subtitle }}<span data-update-time> · {{ updateTime(updatedAt) }}</span></p></div><slot name="selector" /></div>
    <div class="candle-periods segmented" role="group" :aria-label="title + 'K线周期'"><button v-for="item in periods" :key="item.value" :aria-pressed="period === item.value" :class="{ selected: period === item.value }" @click="period = item.value">{{ item.label }}</button></div>
    <div class="candle-frame">
      <CandlePlot v-if="result.bars.length" :candles="result.bars" :period="period" :period-label="periodLabel" :series-label="title" :precision="precision" />
      <div v-else class="candle-empty" role="status"><span>{{ result.error || (loading ? '正在准备真实历史数据…' : error || '暂无可用历史记录') }}</span><button v-if="error && !loading" class="btn" @click="$emit('retry')">重新读取</button></div>
    </div>
    <p class="chart-caption"><span>{{ note }} · {{ points.length }} 条日记录<span v-if="stale"> · 最近有效快照<span v-if="loading">，正在后台更新</span></span></span><span>{{ points.at(-1)?.date ?? '等待历史数据' }}</span></p>
  </div>
</template>
<script setup lang="ts">
import { updateTime } from '../utils/dataQuality'
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { aggregateCandles, type Candle, type CandlePeriod } from '../utils/marketCandles'
const props = withDefaults(defineProps<{ updatedAt?: string | null; title: string; subtitle: string; symbol: string; points: Candle[]; loading?: boolean; error?: string; stale?: boolean; precision?: number; note?: string }>(), { precision: 2, note: '真实日线 · 可缩放、拖动与十字线查看' })
defineEmits<{ retry: [] }>()
const loadPlot = () => import('./CandlePlot.vue')
const CandlePlot = defineAsyncComponent(loadPlot)
onMounted(() => { void loadPlot().catch(() => {}) })
const period = ref<CandlePeriod>('week')
const periods: { value: CandlePeriod; label: string }[] = [{ value: 'day', label: '日K' }, { value: 'week', label: '周K' }, { value: 'month', label: '月K' }, { value: 'year', label: '年K' }]
const periodLabel = computed(() => periods.find(item => item.value === period.value)!.label)
const result = computed(() => { try { return { bars: aggregateCandles(props.points, period.value), error: '' } } catch (error) { return { bars: [], error: error instanceof Error ? error.message : '历史数据无效。' } } })
</script>
<style scoped>
.candle-card{min-width:0}
.candle-periods{margin:0 16px;display:inline-flex}
.candle-frame{min-height:554px;padding:0 16px}
.candle-empty{min-height:554px;display:flex;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap;color:var(--text-2);font-size:13px}
.chart-caption{display:flex;justify-content:space-between;gap:12px;padding:8px 16px 14px;flex-wrap:wrap}
@media(max-width:640px){.candle-frame,.candle-empty{min-height:490px}.panel-head{flex-wrap:wrap;gap:10px}}
</style>
