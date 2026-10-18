<template>
  <PageHeader title="组合分析" subtitle="从收益贡献、配置与集中度，理解当前组合。"><button class="btn" @click="exportReport"><Download :size="15" aria-hidden="true" />导出分析</button><button class="btn primary" :disabled="refreshing" @click="refresh()"><RefreshCw :size="15" aria-hidden="true" />更新分析</button></PageHeader>
  <BackendState :error="error || history.error.value" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <template v-if="data">
    <div class="page-context"><span class="data-badge">{{ session?.user.username }}</span><span>配置与贡献来自当前持仓 · 曲线使用已确认净值</span></div>
    <div class="kpi-grid">
      <div class="panel metric-card"><span class="metric-label">持仓收益率</span><div class="metric-value" :class="profit >= 0 ? 'up' : 'down'">{{ cost ? percent(profit / cost * 100) : '—' }}</div><div class="metric-note">浮动收益 {{ money(profit, true) }}</div></div>
      <div class="panel metric-card"><span class="metric-label">现金占比</span><div class="metric-value">{{ percent(total ? Number(data.portfolio.cash) / total * 100 : 0, false) }}</div><div class="metric-note">可用现金 {{ money(Number(data.portfolio.cash)) }}</div></div>
      <div class="panel metric-card"><span class="metric-label">最大单一持仓占比</span><div class="metric-value">{{ percent(total && largest ? Number(largest.market_value) / total * 100 : 0, false) }}</div><div class="metric-note">{{ largest?.name ?? '暂无持仓' }} · 按当前总资产计算</div></div>
    </div>
    <div class="two-col">
      <ChartCard v-model:range="range" title="净值与基准" subtitle="已确认组合净值；尚无记录时保留空图" :observations="observations" />
      <div class="panel card-pad"><div class="card-title">组合观察</div><p class="subtle">这里为账户结构，不是交易建议。</p><div class="row-list"><div class="row"><span>持仓标的</span><b>{{ positions.length }} 个</b></div><div class="row"><span>股票占比</span><b>{{ allocation[0]?.pct }}</b></div><div class="row"><span>基金占比</span><b>{{ allocation[1]?.pct }}</b></div><div class="row"><span>当前区间</span><span class="tag blue">{{ range }}</span></div></div><RouterLink class="text-action section-space" to="/ledger">前往账本记录交易 <ArrowUpRight :size="13" aria-hidden="true" /></RouterLink></div>
    </div>
    <div class="panel table-panel section-space"><div class="panel-head"><div><h2>资产配置</h2><p>与资产总览使用同一份账户数据。</p></div></div><DataTable table-id="native-allocation" label="账户资产配置" :headers="['资产类别', '市值', '占比']" :rows="allocation.map(item => [item.name, item.value, item.pct])" :numeric-columns="[1, 2]" /></div>
    <div class="panel table-panel section-space"><div class="panel-head"><div><h2>收益贡献</h2><p>按当前持仓浮动收益排列。</p></div><div class="segmented" role="group" aria-label="收益贡献筛选"><button v-for="item in ['全部', '正贡献', '负贡献']" :key="item" :aria-pressed="contribution === item" :class="{ selected: contribution === item }" @click="contribution = item">{{ item }}</button></div></div><DataTable table-id="live-contributions" label="持仓收益贡献" :headers="['名称', '代码', '市值', '浮动收益', '持仓收益率']" :rows="rows" :numeric-columns="[2, 3, 4]" empty-title="当前没有此类收益贡献" /></div>
    <div class="panel table-panel section-space"><div class="panel-head"><h2>已确认组合净值</h2></div><DataTable table-id="native-nav-history" label="已确认组合净值" :headers="['日期', '净值', '总资产', '现金', '外部资金流']" :rows="(history.data.value?.points ?? []).map(item => [item.as_of_date, item.unit_nav, item.total_value, item.cash, item.net_external_flow])" :numeric-columns="[1, 2, 3, 4]" empty-title="尚无已确认净值记录" /></div>
  </template>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { Download, RefreshCw, ArrowUpRight } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import DataTable from './DataTable.vue'
import BackendState from './BackendState.vue'
import { usePortfolioPresentation } from '../composables/usePortfolioPresentation'
import { money, percent, downloadCsv } from '../utils/format'
import ChartCard from './ChartCard.vue'
const { data, error, refreshing, session, refresh, positions, total, cost, profit, allocation, observations, range, history } = usePortfolioPresentation()
const contribution = ref('全部')
const largest = computed(() => [...positions.value].sort((a, b) => Number(b.market_value) - Number(a.market_value))[0])
const rows = computed(() => positions.value.map(item => { const cost = Number(item.average_cost) * Number(item.quantity), profit = Number(item.market_value) - cost; return { item, cost, profit } }).filter(item => contribution.value === '全部' || (contribution.value === '正贡献' ? item.profit >= 0 : item.profit < 0)).sort((a, b) => b.profit - a.profit).map(({ item, cost, profit }) => [item.name, item.symbol, item.market_value, money(profit, true), cost ? percent(profit / cost * 100) : '—']))
function exportReport() { downloadCsv('观澜-组合分析.csv', ['名称', '代码', '市值', '浮动收益', '持仓收益率'], rows.value) }
</script>
