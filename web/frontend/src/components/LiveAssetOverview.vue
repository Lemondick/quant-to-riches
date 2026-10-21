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
    <LiveValuationWidget />
    <LiveRiskWidget />
    <p v-if="data.unavailable.length" class="feedback" role="status">部分公开数据暂不可用，已保留账户持仓。</p>
    <p v-if="stale || refreshing && data" class="feedback" role="status">正在更新，当前显示最近一次有效数据。</p>
    <div class="panel table-panel section-space">
      <div class="panel-head"><div><h2>本币折算</h2><p>按配置汇率把各币种持仓折算为{{ baseCurrency }}。{{ sourcesNote }}</p></div><button class="btn" @click="fxOpen = true"><Coins :size="14" aria-hidden="true" />汇率设置</button></div>
      <div class="card-pad">
        <div class="row-list">
          <div class="row"><span>折算后总资产（{{ baseCurrency }}）</span><b>{{ showMoney(fxTotal) }}</b></div>
          <div v-for="group in fxBreakdown" :key="group.currency" class="row">
            <span>{{ group.currency }} {{ hidden ? '••••' : money(group.amount) }}</span>
            <span v-if="group.missing" class="tag red">缺汇率，未计入折算总值</span>
            <span v-else class="subtle">{{ hidden ? '••••' : money(group.value) }} · 1 {{ group.currency }} = {{ group.rate!.rate }} {{ baseCurrency }}</span>
          </div>
        </div>
        <p class="subtle section-space">汇率来源与时点：<span v-for="group in fxBreakdown.filter(item => !item.missing && item.rate)" :key="group.currency">{{ group.currency }}（{{ group.rate!.source }} · {{ group.rate!.observed_at.slice(0, 16).replace('T', ' ') }}）</span><span v-if="!fxBreakdown.some(item => item.rate)">暂无</span>。持仓币种取自行情币种字段，未匹配到的持仓按本币计；折算汇率保存在浏览器本地，`fx_rates` 表由后端 0030 迁移建立，待后端提供读写接口后可同步。</p>
      </div>
    </div>
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
    <AppDialog v-model="fxOpen" title="汇率设置" description="录入或拉取汇率，用于持仓本币折算。1 单位外币 = 若干本币。">
      <div class="stack-form">
        <p v-if="pullError" class="feedback error" role="alert">{{ pullError }}</p>
        <p v-if="pullNotice" class="feedback" role="status">{{ pullNotice }}</p>
        <p v-if="fxFormError" class="feedback error" role="alert">{{ fxFormError }}</p>
        <div class="form-grid">
          <label>本币<input v-model.trim="baseCurrency" class="field" maxlength="3" placeholder="CNY" /></label>
          <label>拉取地址<input v-model.trim="pullUrl" class="field" placeholder="https://…/rates.json（可选）" /></label>
        </div>
        <div class="inline-actions"><button class="btn" :disabled="pulling" @click="pullRates()">{{ pulling ? '拉取中' : '从拉取地址更新' }}</button></div>
        <form class="stack-form" @submit.prevent="saveFxRate">
          <div class="form-grid">
            <label>币种<input v-model.trim="fxDraft.from" class="field" maxlength="3" placeholder="USD" required /></label>
            <label>汇率（1 外币 = ? 本币）<input v-model.trim="fxDraft.rate" class="field" inputmode="decimal" placeholder="7.12" required /></label>
          </div>
          <div class="form-grid">
            <label>来源<input v-model.trim="fxDraft.source" class="field" maxlength="80" placeholder="手动录入" /></label>
            <label>时点<input v-model="fxDraft.observed_at" class="field" type="datetime-local" required /></label>
          </div>
          <button class="btn primary" type="submit">保存汇率</button>
        </form>
        <div class="row-list">
          <div v-for="entry in fxRates" :key="entry.from" class="row">
            <span><b>{{ entry.from }}</b> → {{ baseCurrency }}：1 = {{ entry.rate }}</span>
            <span class="subtle">{{ entry.source }} · {{ entry.observed_at.slice(0, 16).replace('T', ' ') }}</span>
            <button class="text-action" @click="removeRate(entry.from)">删除</button>
          </div>
          <p v-if="!fxRates.length" class="subtle">暂无汇率，折算将按本币原值显示并提示缺汇率。</p>
        </div>
      </div>
      <template #footer><button class="btn" @click="fxOpen = false">关闭</button></template>
    </AppDialog>
  </template>
</template>
<script setup lang="ts">
import { updateTime, oldestUpdate, usableEstimate } from '../utils/dataQuality'

import { computed, reactive, ref } from 'vue'
import { ArrowUpRight, Coins, Download, Eye, EyeOff, Plus, RefreshCw, Wallet, ChartNoAxesColumnIncreasing, TrendingUp, PieChart } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import StatCard from './StatCard.vue'
import Donut from './Donut.vue'
import DataTable from './DataTable.vue'
import FilterBar from './FilterBar.vue'
import BackendState from './BackendState.vue'
import LiveValuationWidget from './LiveValuationWidget.vue'
import LiveRiskWidget from './LiveRiskWidget.vue'
import AppDialog from './AppDialog.vue'
import { usePortfolioPresentation } from '../composables/usePortfolioPresentation'
import { useLocalState } from '../composables/useLocalState'
import { useFxRates } from '../composables/useFxRates'
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

// F7: 汇率录入/拉取配置 + 持仓本币折算。持仓币种取自行情币种字段（Quote.currency），
// 后端 fx_rates 表由 0030 迁移建立，读写接口尚未提供，故汇率配置暂存浏览器本地。
const fx = useFxRates()
const { baseCurrency, rates: fxRates, convert: fxConvert, upsertRate, removeRate, pullRates, pulling, pullError, pullNotice, pullUrl, sourcesNote } = fx
const fxOpen = ref(false), fxFormError = ref('')
const fxDraft = reactive({ from: '', rate: '', source: '', observed_at: '' })
function currencyOf(symbol: string, assetType: string) {
  const quote = data.value?.quotes.items.find(item => item.symbol === symbol && (!assetType || item.asset_type === assetType))
  return (quote?.currency ?? '').trim().toUpperCase() || null
}
const fxBreakdown = computed(() => {
  const groups = new Map<string, number>()
  for (const item of positions.value) {
    const currency = currencyOf(item.symbol, item.asset_type) ?? baseCurrency.value
    groups.set(currency, (groups.get(currency) ?? 0) + Number(item.market_value))
  }
  return [...groups.entries()].map(([currency, amount]) => { const c = fxConvert(amount, currency); return { currency, amount, value: c.value, rate: c.rate, missing: c.missing } })
})
const fxTotal = computed(() => fxBreakdown.value.reduce((sum, group) => sum + (group.missing ? 0 : group.value), 0))
function saveFxRate() {
  fxFormError.value = ''
  try {
    const rate = Number(fxDraft.rate)
    upsertRate({ from: fxDraft.from, rate, source: fxDraft.source, observed_at: fxDraft.observed_at ? new Date(fxDraft.observed_at).toISOString() : new Date().toISOString() })
    fxDraft.from = ''; fxDraft.rate = ''; fxDraft.source = ''
  } catch (error) { fxFormError.value = error instanceof Error ? error.message : '保存失败。' }
}
</script>
