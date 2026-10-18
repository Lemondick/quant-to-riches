<template>
  <LiveTradeLedger v-if="liveBackend" />
  <template v-else>
  <PageHeader title="交易账本" subtitle="统一记录买入、卖出、分红与资金变动。"><button class="btn" @click="exportLedger"><Download :size="15" aria-hidden="true" />导出账本</button><button class="btn primary" @click="openTrade"><Plus :size="15" aria-hidden="true" />新增交易</button></PageHeader>
  <div class="page-context"><span class="data-badge">本地演示记账</span><span>记账会同步调整演示持仓与现金，不连接券商或执行真实交易。</span></div>
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <div class="kpi-grid">
    <div class="panel metric-card"><span class="metric-label">账户交易记录</span><div class="metric-value">{{ portfolio.transactions.length }} 笔</div><div class="metric-note">当前演示账户的全部记录</div></div>
    <div class="panel metric-card"><span class="metric-label">可用现金</span><div class="metric-value">{{ money(portfolio.cash) }}</div><div class="metric-note">买入、卖出和分红后同步更新</div></div>
    <div class="panel metric-card"><span class="metric-label">记录内已实现收益</span><div class="metric-value" :class="realized >= 0 ? 'up' : 'down'">{{ money(realized, true) }}</div><div class="metric-note">卖出价差与现金分红，已扣记账手续费</div></div>
  </div>
  <div class="panel table-panel section-space">
    <div class="panel-head"><div><h2>交易记录</h2><p>按交易类型、名称、代码或备注查找记录。</p></div><span class="tag blue">现金与持仓联动</span></div>
    <FilterBar v-model="direction" v-model:query="query" :options="['全部', '买入', '卖出', '分红']" class="panel-filters" label="交易类型" placeholder="搜索名称、代码或备注" search-label="搜索交易名称、代码或备注" />
    <DataTable table-id="trade-ledger" label="交易记录" :headers="headers" :rows="rows" :numeric-columns="[4, 5, 6, 7, 8]" :min-width="1100" empty-title="没有符合条件的交易记录">
      <template #summary>显示 <b>{{ rows.length }}</b> / {{ portfolio.transactions.length }} 笔记录</template>
      <template #cell-3="{ value }"><span class="tag" :class="{ red: value === '卖出', blue: value === '分红' }">{{ value }}</span></template>
      <template #empty-action><button class="btn" @click="direction = '全部'; query = ''">清除筛选</button></template>
    </DataTable>
  </div>
  <AppDialog v-model="dialogOpen" title="新增交易记录" description="仅记入本地演示账户，不会向任何交易平台下单。">
    <form id="trade-form" class="stack-form" @submit.prevent="submitTrade">
      <div class="form-grid"><label>交易方向<select v-model="draft.direction" class="field" aria-label="交易方向"><option>买入</option><option>卖出</option><option>分红</option></select></label><label>标的类型<select v-model="draft.type" class="field" aria-label="标的类型"><option>基金</option><option>股票</option></select></label></div>
      <div class="form-grid"><label>六位代码<input v-model.trim="draft.code" class="field" inputmode="numeric" pattern="[0-9]{6}" required /></label><label>标的名称<input v-model.trim="draft.name" class="field" required maxlength="40" /></label></div>
      <div class="form-grid"><label>{{ draft.direction === '分红' ? '分红总金额' : '成交价格' }}<input v-model.number="draft.price" class="field" type="number" min="0.0001" step="any" required /></label><label v-if="draft.direction !== '分红'">成交数量<input v-model.number="draft.quantity" class="field" type="number" min="0.0001" step="any" required /></label><label>手续费<input v-model.number="draft.fee" class="field" type="number" min="0" step="0.01" required /></label></div>
      <label>交易时间（北京时间）<input v-model="tradeDate" class="field" type="datetime-local" required /></label>
      <label>交易备注<input v-model.trim="draft.note" class="field" maxlength="100" placeholder="例如：定投、调仓或现金分红" /></label>
      <div class="form-hint">预计{{ draft.direction === '买入' ? '支出' : '到账' }} {{ money(estimatedAmount) }} · 当前现金 {{ money(portfolio.cash) }}</div>
      <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
    </form>
    <template #footer><button class="btn" @click="dialogOpen = false">取消</button><button class="btn primary" type="submit" form="trade-form">确认记账</button></template>
  </AppDialog>
</template>
</template>
<script setup lang="ts">
import LiveTradeLedger from '../components/LiveTradeLedger.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
import { computed, reactive, ref, watch } from 'vue'
import { Download, Plus } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import FilterBar from '../components/FilterBar.vue'
import AppDialog from '../components/AppDialog.vue'
import { usePortfolio } from '../composables/usePortfolio'
import { fundList } from '../data/funds'
import { money, number, dateTime, localDateInput, downloadCsv } from '../utils/format'
import type { TradeInput } from '../utils/portfolio'
const { portfolio, addTrade } = usePortfolio()
const direction = ref('全部'), query = ref(''), notice = ref(''), dialogOpen = ref(false), formError = ref(''), tradeDate = ref(localDateInput())
const realized = computed(() => portfolio.value.transactions.reduce((sum, trade) => sum + trade.realized, 0))
const headers = ['名称', '时间', '代码', '方向', '成交金额', '成交价格', '数量', '手续费', '已实现收益', '备注']
const rows = computed(() => portfolio.value.transactions.filter(trade => (direction.value === '全部' || trade.direction === direction.value) && (!query.value.trim() || [trade.name, trade.code, trade.note].some(value => value.toLowerCase().includes(query.value.trim().toLowerCase())))).map(trade => [trade.name, dateTime(trade.date), trade.code, trade.direction, money(trade.amount), trade.direction === '分红' ? '—' : number(trade.price, 4), trade.direction === '分红' ? '—' : number(trade.quantity, 4), money(trade.fee), trade.direction === '买入' ? '—' : money(trade.realized, true), trade.note]))
const draft = reactive<TradeInput>({ name: '', code: '', type: '基金', direction: '买入', quantity: 100, price: 1, fee: 0, date: '', note: '' })
watch(() => draft.code, code => {
  const holding = portfolio.value.holdings.find(item => item.code === code)
  const fund = fundList.find(item => item.code === code)
  if (holding) { draft.name = holding.name; draft.type = holding.type; draft.price = holding.price }
  else if (fund) { draft.name = fund.name; draft.type = '基金'; draft.price = Number(fund.nav) }
})
const estimatedAmount = computed(() => {
  const amount = Number(draft.price) * (draft.direction === '分红' ? 1 : Number(draft.quantity))
  return Number.isFinite(amount) ? amount + (draft.direction === '买入' ? Number(draft.fee) : -Number(draft.fee)) : 0
})
function openTrade() { Object.assign(draft, { name: '', code: '', type: '基金', direction: '买入', quantity: 100, price: 1, fee: 0, note: '' }); tradeDate.value = localDateInput(); formError.value = ''; dialogOpen.value = true }
function submitTrade() {
  try { addTrade({ ...draft, quantity: draft.direction === '分红' ? 0 : Number(draft.quantity), date: tradeDate.value + ':00+08:00' }); dialogOpen.value = false; notice.value = '交易已记入本地账户，现金、持仓和组合分析已同步。' }
  catch (error) { formError.value = (error as Error).message }
}
function exportLedger() { downloadCsv('观澜-交易账本.csv', headers, rows.value); notice.value = '已导出当前筛选的交易记录。' }
</script>
