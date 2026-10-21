<template>
  <LivePortfolioAnalysis v-if="liveBackend" />
  <template v-else>
  <PageHeader title="组合分析" subtitle="从收益贡献、配置与集中度，理解当前组合。"><button class="btn" @click="exportReport"><Download :size="15" aria-hidden="true" />导出报告</button><button class="btn primary" @click="notice = '已基于当前本地持仓更新分析。'"><RefreshCw :size="15" aria-hidden="true" />更新分析</button></PageHeader>
  <div class="page-context"><span class="data-badge">演示分析</span><span>配置与贡献来自当前持仓 · 曲线使用演示样本</span></div>
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <div class="kpi-grid">
    <div class="panel metric-card"><span class="metric-label">持仓收益率</span><div class="metric-value" :class="totals.returnRate >= 0 ? 'up' : 'down'">{{ percent(totals.returnRate) }}</div><div class="metric-note">浮动收益 {{ money(totals.profit, true) }}</div></div>
    <div class="panel metric-card"><span class="metric-label">现金占比</span><div class="metric-value">{{ percent(totals.total ? portfolio.cash / totals.total * 100 : 0, false) }}</div><div class="metric-note">可用现金 {{ money(portfolio.cash) }}</div></div>
    <div class="panel metric-card"><span class="metric-label">最大单一持仓占比</span><div class="metric-value">{{ percent(largestWeight, false) }}</div><div class="metric-note">{{ largestHolding?.name ?? '暂无持仓' }} · 按当前总资产计算</div></div>
  </div>
  <div class="two-col">
    <ChartCard v-model:range="range" title="净值与基准" subtitle="各周期展示独立的演示曲线，纵轴为相对涨跌幅" :legends="legends" />
    <div class="panel card-pad"><div class="card-title">组合观察</div><p class="subtle">以下为账户结构，不是交易建议。</p><div class="row-list"><div class="row"><span>持仓标的</span><b>{{ portfolio.holdings.length }} 个</b></div><div class="row"><span>股票占比</span><b>{{ allocation[0]?.[2] }}</b></div><div class="row"><span>基金占比</span><b>{{ allocation[1]?.[2] }}</b></div><div class="row"><span>当前区间</span><span class="tag blue">{{ range }}演示走势</span></div></div><RouterLink class="text-action section-space" to="/ledger">前往账本记录交易 <ArrowUpRight :size="13" aria-hidden="true" /></RouterLink></div>
  </div>
  <div class="panel table-panel section-space"><div class="panel-head"><div><h2>资产配置</h2><p>与资产总览使用同一份账户数据。</p></div></div><DataTable table-id="portfolio" label="资产配置" :headers="['资产类别', '市值', '占比', '持仓成本', '浮动收益']" :rows="allocation" :numeric-columns="[1, 2, 3, 4]" /></div>
  <div class="panel table-panel section-space">
    <div class="panel-head"><div><h2>收益贡献</h2><p>按当前浮动收益排序，可切换查看正负贡献。</p></div><div class="segmented" role="group" aria-label="收益贡献筛选"><button v-for="option in ['全部', '正贡献', '负贡献']" :key="option" :aria-pressed="contribution === option" :class="{ selected: contribution === option }" @click="contribution = option">{{ option }}</button></div></div>
    <DataTable table-id="portfolio-contribution" label="持仓收益贡献" virtual :headers="['名称', '代码', '市值', '浮动收益', '持仓收益率']" :rows="contributions" :numeric-columns="[2, 3, 4]" empty-title="当前没有此类收益贡献" />
  </div>
</template>
</template>
<script setup lang="ts">
import LivePortfolioAnalysis from '../components/LivePortfolioAnalysis.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
import { computed, ref } from 'vue'
import { ArrowUpRight, Download, RefreshCw } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import ChartCard from '../components/ChartCard.vue'
import DataTable from '../components/DataTable.vue'
import { usePortfolio } from '../composables/usePortfolio'
import { money, percent, downloadCsv } from '../utils/format'
const { portfolio, totals } = usePortfolio()
const range = ref('近3月'), contribution = ref('全部'), notice = ref('')
const largestHolding = computed(() => [...portfolio.value.holdings].sort((a, b) => b.quantity * b.price - a.quantity * a.price)[0])
const largestWeight = computed(() => totals.value.total && largestHolding.value ? largestHolding.value.quantity * largestHolding.value.price / totals.value.total * 100 : 0)
const legends = computed(() => [{ name: '组合样本', value: percent(totals.value.returnRate), color: '#4d8dff' }, { name: '沪深300样本', value: '+3.17%', color: '#ff9d54' }])
const allocation = computed(() => ['股票', '基金', '现金'].map(type => {
  const holdings = portfolio.value.holdings.filter(item => item.type === type)
  const value = type === '现金' ? portfolio.value.cash : holdings.reduce((sum, item) => sum + item.quantity * item.price, 0)
  const cost = type === '现金' ? value : holdings.reduce((sum, item) => sum + item.quantity * item.cost, 0)
  return [type, money(value), percent(totals.value.total ? value / totals.value.total * 100 : 0, false), money(cost), money(value - cost, true)]
}))
const contributions = computed(() => [...portfolio.value.holdings].filter(item => contribution.value === '全部' || (contribution.value === '正贡献' ? item.price > item.cost : item.price < item.cost)).sort((a, b) => (b.price - b.cost) * b.quantity - (a.price - a.cost) * a.quantity).map(item => [item.name, item.code, money(item.price * item.quantity), money((item.price - item.cost) * item.quantity, true), percent((item.price / item.cost - 1) * 100)]))
function exportReport() { downloadCsv('观澜-组合分析.csv', ['名称', '代码', '市值', '浮动收益', '持仓收益率'], contributions.value); notice.value = '已导出当前贡献筛选的分析报告。' }
</script>
