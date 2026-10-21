<template>
  <div class="stats-grid">
    <StatCard label="执行率" :value="stats.execution_rate_pct.toFixed(1) + '%'" :delta="`${stats.done_periods} / ${stats.done_periods + stats.missed_periods} 期`" note="已扣款 / 应扣款" :icon="CheckCircle2" />
    <StatCard label="漏投" :value="String(stats.missed_periods)" :delta="tracking.missed_alert_days + ' 天提醒阈值'" note="漏投期数" :icon="AlertTriangle" :tone="stats.missed_periods ? 'red' : ''" />
    <StatCard label="金额偏离" :value="signedMoney(stats.amount_deviation)" :delta="stats.done_periods + ' 期已扣款'" note="实际 − 计划" :icon="Scale" :tone="stats.amount_deviation < 0 ? 'red' : 'green'" />
  </div>
  <div class="panel table-panel">
    <div class="panel-head">
      <div><h2>计划 vs 执行</h2><p>{{ tracking.plan_name }} · 按期/漏投/金额偏离</p></div>
      <button class="btn primary" @click="$emit('register')"><Plus :size="15" aria-hidden="true" />登记扣款</button>
    </div>
    <DataTable table-id="dca-tracking" label="定投计划执行对比" :headers="headers" :rows="rows" :numeric-columns="[2, 3, 4]" :min-width="760" empty-title="该计划暂无执行记录">
      <template #summary>共 <b>{{ rows.length }}</b> 期</template>
      <template #cell-1="{ value }"><span class="tag" :class="statusTone[value as DcaExecutionStatus]">{{ statusName(value as DcaExecutionStatus) }}</span></template>
    </DataTable>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, CheckCircle2, Plus, Scale } from 'lucide-vue-next'
import StatCard from './StatCard.vue'
import DataTable from './DataTable.vue'
import { DCA_STATUS_NAMES, dcaPeriodDeviation, dcaTrackingStats, type DcaExecutionStatus, type DcaTracking } from '../services/batch5Api'
import { money } from '../utils/format'

const props = defineProps<{ tracking: DcaTracking }>()
defineEmits<{ register: [] }>()

const stats = computed(() => dcaTrackingStats(props.tracking))
const headers = ['扣款日期', '状态', '计划金额', '实际金额', '金额偏离', '执行时间', '备注']
const statusTone: Record<DcaExecutionStatus, string> = { pending: '', done: 'blue', missed: 'red', skipped: '' }
function statusName(status: DcaExecutionStatus) { return DCA_STATUS_NAMES[status] }
const rows = computed(() => [...props.tracking.periods]
  .sort((a, b) => b.due_date.localeCompare(a.due_date))
  .map(period => {
    const deviation = dcaPeriodDeviation(period)
    return [
      period.due_date, period.status, money(period.planned_amount),
      period.actual_amount === null ? '—' : money(period.actual_amount),
      deviation === null ? '—' : money(deviation, true),
      period.executed_at ? period.executed_at.slice(0, 16).replace('T', ' ') : '—',
      period.note ?? '—',
    ]
  }))
function signedMoney(value: number) { return money(value, true) }
</script>
