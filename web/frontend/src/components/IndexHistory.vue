<template>
  <CandleChartCard :title="'主要指数走势 · ' + (data?.name ?? names[symbol])" subtitle="真实交易日开高低收，支持日、周、月、年K线" :updated-at="data?.updated_at ?? null" :symbol="symbol" :points="points" :loading="refreshing" :error="error instanceof Error ? error.message : ''" :stale="!!data?.stale || stale" @retry="refresh()">
    <template #selector><select v-model="symbol" class="btn" aria-label="选择指数"><option v-for="(name, code) in names" :key="code" :value="code">{{ name }}</option></select></template>
  </CandleChartCard>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import CandleChartCard from './CandleChartCard.vue'
import { useApiLoader } from '../composables/useApiLoader'
import { createDataApi } from '../services/dataApi'
import { readPreparedPublic } from '../services/preparedPublic'
import type { Candle } from '../utils/marketCandles'
const names: Record<string, string> = { '000300': '沪深300', '000001': '上证指数', '399001': '深证成指', '399006': '创业板指' }
const symbol = ref('000300'), base = import.meta.env.VITE_API_BASE_URL ?? '', api = createDataApi(base)
type History = { symbol: string; name: string; items: { date: string; open: string; high: string; low: string; close: string; volume?: string }[]; updated_at?: string | null; source: string; observed_at: string; stale: boolean }
const { data, error, refreshing, stale, refresh } = useApiLoader(computed(() => `${base}:public:history:${symbol.value}`), async (signal, key) => {
  const code = key.split(':').at(-1)!
  return readPreparedPublic(async () => { const value = await api.read<History>('/market/history?symbol=' + code, signal); if (value.symbol !== code || !Array.isArray(value.items)) throw new Error('指数历史格式无效。'); return value }, signal)
}, 3600000, true)
const points = computed<Candle[]>(() => (data.value?.items ?? []).map(item => ({ date: item.date, open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close), ...(item.volume === undefined ? {} : { volume: Number(item.volume) }) })))
</script>
