<template>
  <div v-if="snapshot">
    <div class="page-context"><span class="data-badge">持有成本</span><span>共 {{ rows.length }} 只基金<span v-if="snapshot.as_of"> · 数据日期 {{ snapshot.as_of }}</span></span></div>
    <div class="stats-grid">
      <StatCard label="持仓总成本" :value="money(totalCost)" delta="全部基金" note="" :icon="Wallet" />
      <StatCard label="浮动盈亏合计" :value="signedMoney(totalPnl)" :delta="profitCount + ' 只盈利 / ' + rows.length + ' 只'" note="" :icon="TrendingUp" />
    </div>
    <div class="panel table-panel">
      <div class="panel-head"><div><h2>持仓成本明细</h2><p>点击行展开查看赎回费率档位与税费提示。</p></div><span v-if="useVirtual" class="tag blue">虚拟渲染 · {{ rows.length }} 行</span></div>
      <VirtualTable v-if="useVirtual" table-id="cost" label="持有成本明细" :headers="headers" :rows="virtualRows"
        :numeric-columns="[1, 2, 3, 4, 5, 6, 7]" :cell-html="virtualCellHtml"
        empty-title="暂无持仓成本数据" empty-text="当前账户暂无基金持仓。" />
      <div v-else class="cost-wrap" :tabindex="rows.length ? 0 : undefined" aria-label="持有成本明细，可横向滚动">
        <table class="cost-table">
          <thead><tr><th v-for="(header, index) in headers" :key="header" scope="col" :class="{ numeric: index > 0 }">{{ header }}</th></tr></thead>
          <tbody>
            <template v-for="row in rows" :key="row.symbol">
              <tr class="cost-row" :tabindex="0" :aria-expanded="expanded === row.symbol" @click="toggle(row.symbol)" @keydown.enter="toggle(row.symbol)">
                <td><b>{{ row.name || row.symbol }}</b><span class="subtle">{{ row.name ? row.symbol : '' }}</span></td>
                <td class="numeric">{{ moneyText(row.cost_total) }}</td>
                <td class="numeric">{{ moneyText(row.price) }}</td>
                <td class="numeric" :class="pnlTone(row.unrealized_pnl)">{{ signedMoneyText(row.unrealized_pnl) }}</td>
                <td class="numeric" :class="pnlTone(row.unrealized_pnl)">{{ rateText(row.unrealized_pnl_rate) }}</td>
                <td class="numeric">{{ rateText(row.annualized_return) }}</td>
                <td class="numeric">{{ moneyText(row.breakeven_price) }}</td>
                <td class="numeric">{{ row.holding_days === null ? '—' : row.holding_days + ' 天' }}</td>
              </tr>
              <tr v-if="expanded === row.symbol" class="cost-expand">
                <td colspan="8">
                  <div class="redeem-box">
                    <b>赎回提示 · {{ row.name || row.symbol }}</b>
                    <ul v-if="redemptionHint(row).tiers.length">
                      <li v-for="tier in redemptionHint(row).tiers" :key="tier">{{ tier }}</li>
                    </ul>
                    <p v-else class="subtle">该基金赎回费率档位暂无返回，以基金合同为准。</p>
                    <p class="subtle">{{ redemptionHint(row).taxNote }}</p>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
        <div v-if="!rows.length" class="empty table-empty" role="status"><FileSearch :size="30" aria-hidden="true" /><b>暂无持仓成本数据</b><span>当前账户暂无基金持仓。</span></div>
      </div>
    </div>
    <p v-if="useVirtual" class="feedback" role="status">超过 300 行已自动切换为虚拟渲染；行点击展开赎回提示在虚拟模式下暂不可用。</p>
  </div>
  <div v-else class="panel card-pad empty-state" role="status">
    <FileSearch :size="30" aria-hidden="true" />
    <h2>暂无持仓成本数据</h2>
    <p>{{ emptyReason }}</p>
  </div>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { FileSearch, Wallet, TrendingUp } from 'lucide-vue-next'
import StatCard from './StatCard.vue'
import VirtualTable from './VirtualTable.vue'
import { money } from '../utils/format'
import { escapeHtml, type TableCell } from '../utils/table'
import { redemptionHintText, type CostPosition, type CostSnapshot } from '../services/batch3Api'

const props = defineProps<{ snapshot: CostSnapshot | null; demo?: boolean }>()
const headers = ['基金', '持仓成本', '现价', '浮动盈亏', '盈亏率', '年化收益', '回本线', '持有天数']
const rows = computed(() => props.snapshot?.positions ?? [])
/** >300 行自动用现有 VirtualTable（沿用 DataTable 的阈值语义）。 */
const useVirtual = computed(() => rows.value.length > 300)
const expanded = ref<string | null>(null)
const emptyReason = computed(() =>
  props.demo ? '演示模式：后端未连接，不展示虚构数字。' : '后端成本接口（api/v1/cost/positions，流 A 并行开发中）尚未就绪或当前账户暂无持仓。')

function toggle(symbol: string) { expanded.value = expanded.value === symbol ? null : symbol }
function num(value: string | null): number | null {
  if (value === null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}
function moneyText(value: string | null): string {
  const parsed = num(value)
  return parsed === null ? '—' : '¥' + parsed.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function signedMoneyText(value: string | null): string {
  const parsed = num(value)
  if (parsed === null) return '—'
  return (parsed > 0 ? '+' : parsed < 0 ? '-' : '') + '¥' + Math.abs(parsed).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function rateText(value: string | null): string {
  const parsed = num(value)
  return parsed === null ? '—' : (parsed > 0 ? '+' : '') + (parsed * 100).toFixed(2) + '%'
}
function pnlTone(value: string | null): string {
  const parsed = num(value)
  return parsed === null ? '' : parsed > 0 ? 'up' : parsed < 0 ? 'down' : ''
}
function signedMoney(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  return money(value, true)
}
const totalCost = computed(() => rows.value.reduce((sum, row) => sum + (num(row.cost_total) ?? 0), 0))
const totalPnl = computed(() => rows.value.reduce((sum, row) => sum + (num(row.unrealized_pnl) ?? 0), 0))
const profitCount = computed(() => rows.value.filter(row => (num(row.unrealized_pnl) ?? 0) > 0).length)
function redemptionHint(row: CostPosition) { return redemptionHintText(row) }

const virtualRows = computed<TableCell[][]>(() => rows.value.map(row => [
  row.name ? `${row.name}（${row.symbol}）` : row.symbol,
  row.cost_total, row.price ?? '', row.unrealized_pnl, row.unrealized_pnl_rate ?? '',
  row.annualized_return ?? '', row.breakeven_price ?? '', row.holding_days ?? '',
]))
function virtualCellHtml(value: TableCell, columnIndex: number): string | null {
  if (columnIndex === 0) return `<b>${escapeHtml(String(value))}</b>`
  if ([3, 4].includes(columnIndex)) {
    const parsed = Number(value)
    const tone = Number.isFinite(parsed) && parsed !== 0 ? (parsed > 0 ? 'up' : 'down') : ''
    const text = columnIndex === 3 ? signedMoneyText(String(value)) : rateText(String(value))
    return `<span class="${tone}">${escapeHtml(text)}</span>`
  }
  if ([1, 2, 6].includes(columnIndex)) return escapeHtml(moneyText(String(value)))
  if (columnIndex === 5) return escapeHtml(rateText(String(value)))
  if (columnIndex === 7) return escapeHtml(value === '' ? '—' : `${value} 天`)
  return null
}
</script>
<style scoped>
.cost-wrap{padding:0 16px 8px;overflow:auto}
.cost-table{width:100%;min-width:1100px;border-collapse:collapse;font-size:13px}
.cost-table th,.cost-table td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line, #223752)}
.cost-table th.numeric,.cost-table td.numeric{text-align:right;font-variant-numeric:tabular-nums}
.cost-row{cursor:pointer}
.cost-row:hover td{background:rgba(77,141,255,.06)}
.cost-row:focus{outline:2px solid #4d8dff;outline-offset:-2px}
.cost-row .subtle{display:block;font-size:11px;color:var(--text-2)}
.cost-expand td{background:rgba(77,141,255,.04)}
.redeem-box{padding:6px 4px;font-size:13px;line-height:1.7}
.redeem-box ul{margin:6px 0;padding-left:20px}
.cost-table td.up{color:#3ddc84}.cost-table td.down{color:#ff7b7b}
.subtle{color:var(--text-2);font-size:12px}
</style>
