<template>
  <PageHeader title="资产总览" subtitle="资产、走势与持仓，一眼看清。">
    <button class="btn" :aria-pressed="hidden" @click="hidden = !hidden"><component :is="hidden ? EyeOff : Eye" :size="15" aria-hidden="true" />{{ hidden ? '显示金额' : '隐藏金额' }}</button>
    <button class="btn" :disabled="refreshing" @click="refresh()"><RefreshCw :size="15" aria-hidden="true" />刷新数据</button>
    <RouterLink class="btn primary" to="/ledger"><Plus :size="15" aria-hidden="true" />录入持仓</RouterLink>
  </PageHeader>
  <BackendState :error="error" :refreshing="refreshing && !data" :has-data="!!data" @retry="refresh()" />
  <template v-if="data">
    <div class="page-context"><span class="data-badge">{{ session?.user.username }}</span><span>持仓、组合与账本同步 · 持仓日期 {{ data.portfolio.as_of_date ?? '暂无记录' }}</span></div>
    <div class="stats-grid">
      <StatCard label="总资产" :value="showMoney(total)" :delta="'现金 ' + showMoney(Number(data.portfolio.cash))" note="当前账户" :icon="Wallet" />
      <StatCard label="今日估算收益" :updated-at="oldestUpdate([...data.quotes.items, ...data.valuations.items])" :value="daily === null ? '—' : showMoney(daily, true)" :delta="daily === null ? '等待有效行情' : hidden ? '金额已隐藏' : '最近有效行情'" note="较昨收" tone="red" :icon="ChartNoAxesColumnIncreasing" />
      <StatCard label="持仓浮动收益" :value="showMoney(profit, true)" :delta="hidden ? '金额已隐藏' : cost ? percent(profit / cost * 100) : '—'" note="相对持仓成本" tone="green" :icon="TrendingUp" />
      <StatCard label="持仓标的" :value="String(positions.length)" :delta="fundCount + ' 只基金 · ' + (positions.length - fundCount) + ' 只股票'" note="" tone="purple" :icon="PieChart" />
    </div>
    <div class="dashboard-grid">
      <ChartCard v-model:range="range" title="组合走势" :updated-at="history.data.value?.points.at(-1)?.as_of_date ?? null" :subtitle="hidden ? '金额与收益已隐藏' : '已确认组合净值的实际记录'" :observations="hidden ? emptyChart : observations" variant="portfolio" />
      <div class="panel donut-panel"><div class="panel-head"><div><h2>持仓构成</h2><p>按当前市值计算，包含账户现金<span data-update-time> · {{ updateTime(data.portfolio.as_of_date) }}</span></p></div><span class="tag blue">按市值</span></div><Donut :center="String(positions.length)" label="个持仓标的" :items="hidden ? maskedAllocation : allocation" /></div>
    </div>
    <div class="insight-strip"><span>今日收益主要来源：<b>{{ hidden ? '已隐藏' : positions.length ? '查看持仓明细' : '暂无持仓' }}</b></span><RouterLink to="/portfolio" class="text-action">查看收益贡献 <ArrowUpRight :size="13" aria-hidden="true" /></RouterLink></div>
    <p v-if="data.unavailable.length" class="feedback" role="status">部分公开数据暂不可用，已保留账户持仓。</p>
    <p v-if="stale || refreshing && data" class="feedback" role="status">正在更新，当前显示最近一次有效数据。</p>
    <div class="panel table-panel">
      <div class="panel-head"><div><h2>持有明细</h2><p>录入现有持仓用于观察；买卖记录请前往交易账本。<span data-update-time> · {{ updateTime(data.portfolio.as_of_date) }}</span></p></div><button class="btn" :disabled="hidden" @click="exportHoldings"><Download :size="14" aria-hidden="true" />导出持仓</button></div>
      <FilterBar v-model="category" v-model:query="query" :options="['全部', '基金', '股票']" class="panel-filters" label="持仓类型" placeholder="搜索持仓名称或代码" search-label="搜索持仓名称或代码" />
      <DataTable table-id="live-positions" label="账户持有明细" :headers="headers" :rows="rows" :numeric-columns="[2, 3, 4, 5, 6, 7]" :min-width="1100" empty-title="当前账户暂无持仓">
        <template #summary>显示 <b>{{ rows.length }}</b> / {{ positions.length }} 项持仓</template>
        <template #cell-0="{ value }"><b>{{ value }}</b></template>
        <template #cell-8><RouterLink to="/ledger" class="text-action">记录交易</RouterLink></template>
        <template #empty-action><RouterLink class="btn" to="/ledger">录入持仓</RouterLink></template>
      </DataTable>
    </div>
  </template>
</template>
<script setup lang="ts">
import { updateTime, oldestUpdate, usableEstimate } from '../utils/dataQuality'

import { computed, ref } from 'vue'
import { ArrowUpRight, Download, Eye, EyeOff, Plus, RefreshCw, Wallet, ChartNoAxesColumnIncreasing, TrendingUp, PieChart } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import StatCard from './StatCard.vue'
import Donut from './Donut.vue'
import DataTable from './DataTable.vue'
import FilterBar from './FilterBar.vue'
import BackendState from './BackendState.vue'
import { usePortfolioPresentation } from '../composables/usePortfolioPresentation'
import { useLocalState } from '../composables/useLocalState'
import { money, percent, downloadCsv } from '../utils/format'
import ChartCard from './ChartCard.vue'
const { data, error, refreshing, stale, refresh, session, positions, total, cost, profit, daily, allocation, observations, range, history } = usePortfolioPresentation()
const hidden = useLocalState('guanlan.hide-values.v1', false, (value): value is boolean => typeof value === 'boolean')
const category = ref('全部'), query = ref('')
const emptyChart = { labels: [], series: [] }
const showMoney = (value: number, signed = false) => hidden.value ? '••••' : money(value, signed)
const fundCount = computed(() => positions.value.filter(item => item.asset_type === 'fund').length)
const maskedAllocation = computed(() => allocation.value.map(item => ({ ...item, value: '••••', pct: '••••', weight: 0 })))
const headers = ['名称', '代码', '现价 / 净值', '持仓金额', '持仓数量', '仓位', '今日盈亏', '浮动收益', '操作']
const rows = computed(() => positions.value.filter(item => (category.value === '全部' || item.asset_type === (category.value === '基金' ? 'fund' : 'stock')) && (item.name + ' ' + item.symbol).toLowerCase().includes(query.value.trim().toLowerCase())).map(item => {
  const value = Number(item.market_value), quantity = Number(item.quantity)
  const quote = data.value?.valuations.items.find(quote => quote.symbol === item.symbol) ?? data.value?.quotes.items.find(quote => quote.symbol === item.symbol && quote.asset_type === item.asset_type)
  const usable = quote && quote.quality_status === 'valid' && !quote.is_stale && (!('estimate_nav' in quote) || usableEstimate(quote))
  const rate = usable ? Number(quote.change_rate) / 100 : null
  const observedPrice = usable ? Number('estimate_nav' in quote ? quote.estimate_nav : quote.price) : null
  const dailyValue = rate !== null && rate > -1 && observedPrice !== null ? quantity * observedPrice * rate / (1 + rate) : null
  return [item.name, item.symbol, hidden.value ? '••••' : quantity ? (value / quantity).toFixed(4) : '—', hidden.value ? '••••' : item.market_value, hidden.value ? '••••' : item.quantity, hidden.value ? '••••' : percent(total.value ? value / total.value * 100 : 0, false), dailyValue === null ? '—' : showMoney(dailyValue, true), showMoney(value - Number(item.average_cost) * quantity, true), item.symbol]
}))
function exportHoldings() { if (!hidden.value) downloadCsv('观澜-持仓明细.csv', headers.slice(0, -1), rows.value.map(row => row.slice(0, -1))) }
</script>
