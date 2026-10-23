<template>
  <div class="stats-grid">
    <StatCard :label="positionsOnly ? '账本原币记录' : '总资产'" :value="formatAmount(snapshot.total_assets)" :delta="positionsOnly ? '持仓金额记录合计，未折算汇率' : '含负债口径'" note="快照时点" :icon="Wallet" />
    <StatCard label="净资产" :value="positionsOnly ? '未计算' : money(snapshot.net_assets)" :delta="positionsOnly ? '尚未登记负债' : equityDelta" note="总资产 − 负债" :icon="TrendingUp" tone="green" />
    <StatCard label="负债" :value="positionsOnly ? '未登记' : money(snapshot.total_assets - snapshot.net_assets)" :delta="positionsOnly ? '未确认负债金额' : '总资产 − 净资产'" :note="positionsOnly ? '负债信息不完整，无法确认净资产' : snapshot.liabilities_note ?? '估算口径'" :icon="Scale" />
  </div>
  <div class="dashboard-grid">
    <ChartCard :title="positionsOnly ? '持仓市值历史曲线' : '净资产历史曲线'" :subtitle="positionsOnly ? '原币账本记录，未折算汇率或扣除负债' : '已确认净资产记录'" :updated-at="snapshot.as_of" :observations="observations" variant="line" />
    <div class="panel donut-panel">
      <div class="panel-head"><div><h2>{{ positionsOnly ? '账本记录分类' : '资产配置' }}</h2><p>{{ positionsOnly ? '原币金额记录占比，未做币种折算' : '按快照金额计算' }}<span data-update-time> · {{ snapshot.as_of ?? '暂无记录' }}</span></p></div><span class="tag blue">{{ donutItems.length }} 类</span></div>
      <Donut :center="shortMoney(positionsOnly ? snapshot.total_assets : snapshot.net_assets)" :label="positionsOnly ? '记录合计' : '净资产'" :items="donutItems" />
    </div>
  </div>
  <p v-if="positionsOnly" class="feedback" role="status">账本记录未做汇率折算，原币金额合计与占比不能用于跨币种资产比较。</p>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { TrendingUp, Wallet, Scale } from 'lucide-vue-next'
import StatCard from './StatCard.vue'
import Donut from './Donut.vue'
import ChartCard from './ChartCard.vue'
import { allocationDonutItems, netWorthObservations, type NetWorthSnapshot } from '../services/batch5Api'
import { money } from '../utils/format'

const props = defineProps<{ snapshot: NetWorthSnapshot }>()
const positionsOnly = computed(() => props.snapshot.liabilities_recorded === false)
const formatAmount = (value: number) => positionsOnly.value ? value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : money(value)
const donutItems = computed(() => allocationDonutItems(props.snapshot, formatAmount))
const observations = computed(() => {
  if (!props.snapshot.history.length) return { labels: [], series: [] }
  const chart = netWorthObservations(props.snapshot)
  return positionsOnly.value ? { ...chart, series: chart.series.map(series => ({ ...series, name: '账本记录' })) } : chart
})
const equityDelta = computed(() => {
  const history = props.snapshot.history
  if (history.length < 2) return '暂无历史'
  const first = history[0].net_assets, last = history[history.length - 1].net_assets
  if (!first) return '暂无历史'
  return ((last / first - 1) * 100 >= 0 ? '+' : '') + ((last / first - 1) * 100).toFixed(2) + '%（区间）'
})
function shortMoney(value: number) {
  if (Math.abs(value) >= 1e8) return (value / 1e8).toFixed(1) + '亿'
  if (Math.abs(value) >= 1e4) return (value / 1e4).toFixed(0) + '万'
  return formatAmount(value)
}
</script>
