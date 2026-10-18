<template>
  <section class="section-space" aria-label="基金公开资料"><p v-if="error" class="feedback error" role="alert">{{ error instanceof Error ? error.message : '基金资料暂不可用。' }}</p><div class="two-col">
    <CandleChartCard :title="data?.name ?? '基金资料 ' + symbol" subtitle="官方公布净值，日K为单日净值，周月K按实际净值聚合" :updated-at="data?.nav_updated_at ?? null" :symbol="symbol" :points="candles" :precision="4" :loading="refreshing" :error="error instanceof Error ? error.message : ''" :stale="!!data?.stale || stale" :note="navFreshnessLabel(data?.nav_metadata) + ' · 净值K线，无盘中成交价及成交量'" @retry="refresh()" />
    <div class="panel card-pad"><div class="detail-title"><div><h2 class="card-title">基金资料</h2><p class="subtle">{{ symbol }} · {{ data?.fund_type ?? '类型未提供' }}<span data-update-time> · {{ updateTime(data?.updated_at) }}</span></p></div><button class="btn" :disabled="refreshing" @click="refresh()">刷新资料</button></div><div class="row-list"><div class="row"><span>最近官方净值</span><b>{{ data?.nav_history.at(-1)?.unit_nav ?? '—' }}</b></div><div class="row"><span>净值日期</span><b>{{ data?.nav_history.at(-1)?.nav_date ?? '—' }}</b></div><div class="row"><span>管理人</span><b>{{ data?.company ?? '—' }}</b></div><div class="row"><span>数据状态</span><span class="tag blue">{{ !data ? refreshing ? '正在准备' : '等待重试' : data.stale || stale ? '历史快照' : data.nav_history.length ? navFreshnessLabel(data.nav_metadata) : '暂无净值观测' }}</span></div></div><p class="form-hint section-space">观测时间 {{ data?.observed_at ?? (refreshing ? '正在读取' : '暂无观测') }}。数据只用于展示与复核。</p></div>
  </div></section>
</template>
<script setup lang="ts">
import { navFreshnessLabel, updateTime } from '../utils/dataQuality'
import { computed } from 'vue'
import { useApiLoader } from '../composables/useApiLoader'
import { fundDataKey, readFundDetail } from '../services/fundData'
import CandleChartCard from './CandleChartCard.vue'
import { navCandles } from '../utils/marketCandles'
const props = defineProps<{ symbol: string }>()
const { data, error, refreshing, stale, refresh } = useApiLoader(computed(() => fundDataKey(props.symbol)), (signal, key) => readFundDetail(key.split(':').at(-1)!, signal), 300000, true)
const candles = computed(() => navCandles(data.value?.nav_history ?? []))
</script>
