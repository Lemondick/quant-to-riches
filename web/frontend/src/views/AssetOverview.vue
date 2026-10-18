<template>
  <LiveAssetOverview v-if="liveBackend" />
  <template v-else>
  <PageHeader title="资产总览" subtitle="资产、走势与持仓，一眼看清。">
    <button class="btn" :aria-pressed="hidden" @click="hidden = !hidden"><component :is="hidden ? EyeOff : Eye" :size="15" aria-hidden="true" />{{ hidden ? '显示金额' : '隐藏金额' }}</button>
    <button class="btn" @click="refresh"><RefreshCw :size="15" aria-hidden="true" />刷新数据</button>
    <button class="btn primary" @click="editHolding()"><Plus :size="15" aria-hidden="true" />录入持仓</button>
  </PageHeader>
  <div class="page-context"><span class="data-badge">本地演示账户</span><span>持仓、组合与账本同步 · 最近查看 {{ dateTime(checkedAt) }}</span></div>
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <div class="stats-grid">
    <StatCard label="总资产" :value="showMoney(totals.total)" :delta="'现金 ' + showMoney(portfolio.cash)" note="当前账户" :icon="Wallet" />
    <StatCard label="今日估算收益" :value="showMoney(totals.daily, true)" :delta="hidden ? '金额已隐藏' : percent(totals.invested - totals.daily > 0 ? totals.daily / (totals.invested - totals.daily) * 100 : 0)" note="较昨收" tone="red" :icon="ChartNoAxesColumnIncreasing" />
    <StatCard label="持仓浮动收益" :value="showMoney(totals.profit, true)" :delta="hidden ? '金额已隐藏' : percent(totals.returnRate)" note="相对持仓成本" tone="green" :icon="TrendingUp" />
    <StatCard label="持仓标的" :value="String(portfolio.holdings.length)" :delta="fundCount + ' 只基金 · ' + (portfolio.holdings.length - fundCount) + ' 只股票'" note="" tone="purple" :icon="PieChart" />
  </div>
  <div class="dashboard-grid">
    <ChartCard title="组合走势" subtitle="以当前账户收益为锚点的演示走势" :legends="chartLegends" variant="portfolio" />
    <div class="panel donut-panel"><div class="panel-head"><div><h2>持仓构成</h2><p>按当前市值计算，包含账户现金</p></div><span class="tag blue">按市值</span></div><Donut :center="String(portfolio.holdings.length)" label="个持仓标的" :items="allocation" /></div>
  </div>
  <div class="insight-strip"><span>今日收益主要来源：<b>{{ leadingHolding?.name ?? '暂无持仓' }}</b></span><RouterLink to="/portfolio" class="text-action">查看收益贡献 <ArrowUpRight :size="13" aria-hidden="true" /></RouterLink></div>
  <div class="panel table-panel">
    <div class="panel-head"><div><h2>持有明细</h2><p>录入现有持仓用于观察；买卖记录请前往交易账本。</p></div><button class="btn" @click="exportHoldings"><Download :size="14" aria-hidden="true" />导出持仓</button></div>
    <FilterBar v-model="category" v-model:query="query" :options="['全部', '基金', '股票']" class="panel-filters" label="持仓类型" placeholder="搜索持仓名称或代码" search-label="搜索持仓名称或代码" />
    <DataTable table-id="holdings" label="持仓明细" :headers="headers" :rows="rows" :numeric-columns="[2, 3, 4, 5, 6, 7]" :min-width="1100" empty-title="暂无符合条件的持仓">
      <template #summary>显示 <b>{{ rows.length }}</b> / {{ portfolio.holdings.length }} 项持仓</template>
      <template #cell-0="{ value }"><b>{{ value }}</b></template>
      <template #cell-8="{ row }"><button class="text-action" :aria-label="'编辑持仓：' + row[0]" @click="editHolding(String(row[1]))">编辑</button></template>
      <template #empty-action><button class="btn" @click="category = '全部'; query = ''">清除筛选</button></template>
    </DataTable>
  </div>
  <AppDialog v-model="dialogOpen" :title="editingCode ? '编辑持仓' : '录入持仓'" description="录入现有持仓不会扣减账户现金。交易买卖请使用交易账本。">
    <form id="holding-form" class="stack-form" @submit.prevent="submitHolding">
      <div class="form-grid"><label>标的名称<input v-model.trim="draft.name" class="field" required maxlength="40" /></label><label>六位代码<input v-model.trim="draft.code" class="field" inputmode="numeric" pattern="[0-9]{6}" required /></label></div>
      <div class="form-grid"><label>标的类型<select v-model="draft.type" class="field" aria-label="标的类型"><option>基金</option><option>股票</option></select></label><label>持仓数量<input v-model.number="draft.quantity" class="field" type="number" min="0.0001" step="any" required /></label></div>
      <div class="form-grid"><label>当前价格 / 净值<input v-model.number="draft.price" class="field" type="number" min="0.0001" step="any" required /></label><label>平均成本<input v-model.number="draft.cost" class="field" type="number" min="0.0001" step="any" required /></label></div>
      <label>昨收价 / 净值<input v-model.number="draft.previousClose" class="field" type="number" min="0.0001" step="any" required /></label>
      <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
    </form>
    <template #footer><button class="btn" @click="dialogOpen = false">取消</button><button class="btn primary" type="submit" form="holding-form">保存持仓</button></template>
  </AppDialog>
  </template>
</template>
<script setup lang="ts">
import LiveAssetOverview from '../components/LiveAssetOverview.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()

import { computed, reactive, ref } from 'vue'
import { ArrowUpRight, Download, Eye, EyeOff, Plus, RefreshCw, Wallet, ChartNoAxesColumnIncreasing, TrendingUp, PieChart } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import StatCard from '../components/StatCard.vue'
import ChartCard from '../components/ChartCard.vue'
import Donut from '../components/Donut.vue'
import DataTable from '../components/DataTable.vue'
import FilterBar from '../components/FilterBar.vue'
import AppDialog from '../components/AppDialog.vue'
import { usePortfolio } from '../composables/usePortfolio'
import { useLocalState } from '../composables/useLocalState'
import { money, percent, number, dateTime, downloadCsv } from '../utils/format'
import type { Holding } from '../utils/portfolio'
const { portfolio, totals, saveHolding } = usePortfolio()
const hidden = useLocalState('guanlan.hide-values.v1', false, (value): value is boolean => typeof value === 'boolean')
const category = ref('全部'), query = ref(''), notice = ref(''), checkedAt = ref(Date.now())
const showMoney = (value: number, signed = false) => hidden.value ? '••••' : money(value, signed)
const fundCount = computed(() => portfolio.value.holdings.filter(item => item.type === '基金').length)
const leadingHolding = computed(() => [...portfolio.value.holdings].sort((a, b) => (b.price - b.previousClose) * b.quantity - (a.price - a.previousClose) * a.quantity)[0])
const chartLegends = computed(() => [{ name: '本组合', value: percent(totals.value.returnRate), color: '#4d8dff' }, { name: '沪深300样本', value: '+3.17%', color: '#ff9d54' }])
const allocation = computed(() => ['股票', '基金', '现金'].map((type, index) => {
  const value = type === '现金' ? portfolio.value.cash : portfolio.value.holdings.filter(item => item.type === type).reduce((sum, item) => sum + item.price * item.quantity, 0)
  return { name: type, value: showMoney(value), pct: percent(totals.value.total ? value / totals.value.total * 100 : 0, false), color: ['#4d8dff', '#ff9d54', '#42ddb1'][index], weight: value }
}))
const headers = ['名称', '代码', '现价 / 净值', '持仓金额', '持仓数量', '仓位', '今日盈亏', '浮动收益', '操作']
const rows = computed(() => portfolio.value.holdings.filter(item => (category.value === '全部' || item.type === category.value) && (!query.value.trim() || item.name.includes(query.value.trim()) || item.code.includes(query.value.trim()))).map(item => [item.name, item.code, hidden.value ? '••••' : number(item.price, 4), showMoney(item.price * item.quantity), hidden.value ? '••••' : number(item.quantity, 4), percent(totals.value.total ? item.price * item.quantity / totals.value.total * 100 : 0, false), showMoney((item.price - item.previousClose) * item.quantity, true), showMoney((item.price - item.cost) * item.quantity, true), item.code]))
function refresh() { checkedAt.value = Date.now(); notice.value = '已重新读取本地账户，未请求实时行情。' }
function exportHoldings() {
  if (hidden.value) { notice.value = '请先显示金额，再导出完整持仓数据。'; return }
  downloadCsv('观澜-持仓明细.csv', headers.slice(0, -1), rows.value.map(row => row.slice(0, -1)))
}
const dialogOpen = ref(false), editingCode = ref<string>(), formError = ref('')
const draft = reactive<Holding>({ name: '', code: '', type: '基金', quantity: 100, price: 1, cost: 1, previousClose: 1 })
function editHolding(code?: string) {
  editingCode.value = code; formError.value = ''
  Object.assign(draft, portfolio.value.holdings.find(item => item.code === code) ?? { name: '', code: '', type: '基金', quantity: 100, price: 1, cost: 1, previousClose: 1 })
  dialogOpen.value = true
}
function submitHolding() {
  try { saveHolding({ ...draft }, editingCode.value); dialogOpen.value = false; notice.value = '持仓已保存，资产与组合分析已同步。' }
  catch (error) { formError.value = (error as Error).message }
}
</script>
