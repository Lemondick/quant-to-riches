<template>
  <PageHeader title="交易账本" subtitle="统一记录买入、卖出、分红与资金变动。"><button class="btn" @click="exportLedger"><Download :size="15" />导出账本</button><button class="btn primary" aria-label="新增记录" @click="open = true"><Plus :size="15" />新增交易</button></PageHeader>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p><p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
  <template v-if="data">
    <div class="page-context"><span class="data-badge">{{ session?.user.username }}</span><span>记账会同步更新当前账户持仓与现金。</span></div>
    <div class="kpi-grid"><div class="panel metric-card"><span class="metric-label">账户交易记录</span><div class="metric-value">{{ data.entries.length }} 笔</div><div class="metric-note">当前账户最近记录</div></div><div class="panel metric-card"><span class="metric-label">可用现金</span><div class="metric-value">{{ summary.data.value?.portfolio.cash ?? '—' }}</div><div class="metric-note">买入、卖出和分红后同步更新</div></div><div class="panel metric-card"><span class="metric-label">记录内已确认费用</span><div class="metric-value">{{ data.total_fees }}</div><div class="metric-note">分红总额 {{ data.total_dividends }}</div></div></div>
    <div class="panel table-panel section-space"><div class="panel-head"><div><h2>交易记录</h2><p>按交易类型、名称、代码或备注查找记录。</p></div><span class="tag blue">现金与持仓联动</span></div><FilterBar v-model="direction" v-model:query="query" :options="['全部', '买入', '卖出', '分红', '定投']" class="panel-filters" label="交易类型" placeholder="搜索名称、代码或备注" search-label="搜索交易名称、代码或备注" /><DataTable table-id="native-ledger" label="账户交易账本" :headers="headers" :rows="rows" :numeric-columns="[4, 5, 6, 7]" :min-width="1100" empty-title="当前账户暂无账本记录"><template #summary>显示 <b>{{ rows.length }}</b> / {{ data.entries.length }} 笔记录</template><template #cell-3="{ value }"><span class="tag" :class="{ red: value === '卖出', blue: value === '分红' }">{{ value }}</span></template></DataTable></div>
  </template>
  <AppDialog v-model="open" title="新增交易记录" description="记录已发生的交易或分红，确认后更新当前账户。">
    <form v-if="session" id="native-ledger-form" class="stack-form" aria-label="新增账户账本记录" @submit.prevent="save">
      <div class="form-grid"><label>记录类型<select v-model="draft.entry_type" class="field"><option value="buy">买入</option><option value="sell">卖出</option><option value="dca">定投</option><option value="dividend">分红</option></select></label><label>资产类型<select v-model="draft.asset_type" class="field"><option value="fund">基金</option><option value="stock">股票</option></select></label></div>
      <div class="form-grid"><label>标的代码<input v-model.trim="draft.symbol" class="field" required maxlength="12" /></label><label v-if="draft.entry_type !== 'dividend'">标的名称<input v-model.trim="draft.name" class="field" required maxlength="80" /></label></div>
      <div class="form-grid"><template v-if="draft.entry_type !== 'dividend'"><label>成交数量<input v-model.trim="draft.quantity" class="field" inputmode="decimal" required /></label><label>成交价格<input v-model.trim="draft.price" class="field" inputmode="decimal" required /></label></template><label v-else>分红总金额<input v-model.trim="draft.gross_amount" class="field" inputmode="decimal" required /></label><label>费用<input v-model.trim="draft.fees" class="field" inputmode="decimal" required /></label></div>
      <label>发生时间（上海）<input v-model="draft.occurred_at" class="field" type="datetime-local" required /></label><label>备注<input v-model.trim="draft.note" class="field" maxlength="500" /></label><button class="btn primary" :disabled="mutating" type="submit">{{ mutating ? '正在保存' : '保存到当前账户' }}</button>
    </form><template #footer><button class="btn" @click="open = false">取消</button></template>
  </AppDialog>
</template>
<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Download, Plus } from 'lucide-vue-next'
import AppDialog from './AppDialog.vue'
import FilterBar from './FilterBar.vue'
import { downloadCsv } from '../utils/format'
import { useDashboardSummary } from '../composables/useDashboardSummary'
import PageHeader from './PageHeader.vue'
import DataTable from './DataTable.vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
type Entry = { id: number; occurred_at: string; name: string; symbol: string; entry_type: string; quantity: string | null; price: string | null; fees: string; net_amount: string; reversed: boolean }
const labels: Record<string, string> = { buy: '买入', sell: '卖出', dca: '定投', dividend: '分红' }
const { data, error, refreshing, refresh, session, mutate, mutating } = useAccountResource('ledger', (api, signal) => api.read<{ entries: Entry[]; total_fees: string; total_dividends: string }>('/ledger?limit=200', signal))
const summary = useDashboardSummary(), direction = ref('全部'), query = ref('')
const headers = ['时间', '名称', '代码', '类型', '数量', '价格', '费用', '净金额', '状态']
const rows = computed(() => (data.value?.entries ?? []).filter(item => (direction.value === '全部' || labels[item.entry_type] === direction.value) && (item.name + item.symbol).includes(query.value.trim())).map(item => [item.occurred_at, item.name, item.symbol, labels[item.entry_type] ?? item.entry_type, item.quantity ?? '—', item.price ?? '—', item.fees, item.net_amount, item.reversed ? '已冲正' : '已记录']))
function exportLedger() { downloadCsv('观澜-交易账本.csv', headers, rows.value) }
const open = ref(false), notice = ref(''), formError = ref('')
const initial = () => ({ entry_type: 'buy', asset_type: 'fund', symbol: '', name: '', quantity: '', price: '', gross_amount: '', fees: '0', occurred_at: new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai' }).slice(0, 16).replace(' ', 'T'), note: '', external_id: `web-${crypto.randomUUID()}` })
const draft = reactive(initial())
watch(() => session.value?.cache_scope, () => { open.value = false; Object.assign(draft, initial()); notice.value = ''; formError.value = '' })
async function save() {
  formError.value = ''; notice.value = ''
  try {
    const decimal = /^(?:0|[1-9]\d*)(?:\.\d{1,8})?$/
    if (![draft.fees, ...(draft.entry_type === 'dividend' ? [draft.gross_amount] : [draft.quantity, draft.price])].every(value => decimal.test(value))) throw new Error('金额、份额和价格须为非负十进制数字，最多八位小数。')
    const common = { symbol: draft.symbol, asset_type: draft.asset_type, fees: draft.fees, occurred_at: `${draft.occurred_at}:00+08:00`, note: draft.note || null, external_id: draft.external_id }
    if (draft.entry_type === 'dividend') await mutate('/ledger/dividends', { ...common, gross_amount: draft.gross_amount })
    else await mutate('/ledger/transactions', { ...common, entry_type: draft.entry_type, name: draft.name, quantity: draft.quantity, price: draft.price })
    Object.assign(draft, initial()); open.value = false; notice.value = '记录已保存到当前账户。'
  } catch (failure) { formError.value = failure instanceof Error ? failure.message : '记录保存失败。' }
}
</script>
