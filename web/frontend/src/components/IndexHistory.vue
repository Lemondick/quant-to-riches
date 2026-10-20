<template>
  <CandleChartCard :title="'主要指数走势 · ' + (selected?.name ?? '请选择指数')" subtitle="真实交易日开高低收，支持日、周、月、年K线" :updated-at="history?.updated_at ?? null" :symbol="indexId" :points="points" :loading="refreshing" :error="historyError" :stale="!!history?.stale || stale" @retry="refresh()">
    <template #selector><select v-model="indexId" class="btn" aria-label="选择指数"><option v-for="item in enabled" :key="item.id" :value="item.id">{{ item.name }}{{ item.product_status === 'provisional' ? '（暂定）' : '' }}</option></select></template>
  </CandleChartCard>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import CandleChartCard from './CandleChartCard.vue'
import { useApiLoader } from '../composables/useApiLoader'
import { createDataApi, type IndexDefinition } from '../services/dataApi'
import { readPreparedPublic } from '../services/preparedPublic'
import { indexHistoryResponse } from '../utils/indexPresentation'
import type { Candle } from '../utils/marketCandles'
const props = withDefaults(defineProps<{ definitions: IndexDefinition[]; registryVersion?: string }>(), { registryVersion: '' })
const enabled = computed(() => [...props.definitions].filter(item => item.enabled).sort((a, b) => a.display_order - b.display_order))
const indexId = ref(''), base = import.meta.env.VITE_API_BASE_URL ?? '', api = createDataApi(base)
watch(enabled, items => { if (!items.some(item => item.id === indexId.value)) indexId.value = (items.find(item => item.legacy_history_symbol === '000300') ?? items[0])?.id ?? '' }, { immediate: true })
const selected = computed(() => enabled.value.find(item => item.id === indexId.value))
const { data, error, refreshing, stale, refresh } = useApiLoader(computed(() => indexId.value ? `${base}:public:history:${props.registryVersion}:${indexId.value}` : null), async (signal, key) => {
  const id = key.split(':').at(-1)!
  return readPreparedPublic(async () => indexHistoryResponse(await api.read('/market/history?index_id=' + encodeURIComponent(id), signal), id), signal)
}, 3600000, true)
const history = computed(() => data.value?.id === indexId.value ? data.value : undefined)
const historyError = computed(() => history.value?.quality_status === 'invalid' ? '指数历史数据未通过质量校验。' : history.value?.quality_reason === 'history_not_connected' ? '历史行情尚未接入' : error.value instanceof Error ? error.value.message : '')
const points = computed<Candle[]>(() => (history.value?.quality_status === 'unavailable' || history.value?.quality_status === 'invalid' ? [] : history.value?.items ?? []).map(item => ({ date: item.date, open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close), ...(item.volume === undefined ? {} : { volume: Number(item.volume) }) })))
</script>
