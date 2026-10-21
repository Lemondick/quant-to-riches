<template>
  <PageHeader title="报告任务状态" subtitle="定时报告的调度状态与任务进度；轮询带退避与抖动，避免早间窗口集中刷新。">
    <span class="tag" :class="{ green: schedule.data.value?.running }">调度器：{{ schedule.data.value ? (schedule.data.value.running ? '运行中' : '未运行') : '—' }}</span>
    <button class="btn" :disabled="polling" @click="manualRefresh"><RefreshCw :size="15" aria-hidden="true" />立即刷新</button>
  </PageHeader>
  <BackendState :error="schedule.error.value" :refreshing="schedule.refreshing.value && !schedule.data.value" :has-data="!!schedule.data.value" @retry="manualRefresh()" />
  <template v-if="schedule.data.value">
    <div class="page-context"><span class="data-badge">{{ session?.user.username }}</span><span>自动刷新：{{ autoRefresh ? `开（${nextIn} 秒后，退避轮次 ${backoffRound}）` : '关' }}</span><button class="text-action" @click="autoRefresh = !autoRefresh">{{ autoRefresh ? '暂停自动刷新' : '恢复自动刷新' }}</button></div>
    <div class="panel card-pad">
      <div class="detail-title"><h2 class="card-title">调度状态</h2><span class="tag" :class="{ red: !schedule.data.value.trading_day }">{{ schedule.data.value.trading_day ? '交易日' : '非交易日' }}</span></div>
      <div class="row-list">
        <div class="row"><span>时区</span><b>{{ schedule.data.value.timezone }}</b></div>
        <div class="row"><span>早间窗口</span><b>{{ schedule.data.value.schedules?.morning ?? '—' }}</b></div>
        <div class="row"><span>午间窗口</span><b>{{ schedule.data.value.schedules?.afternoon ?? '—' }}</b></div>
        <div class="row"><span>日历状态</span><b>{{ schedule.data.value.calendar_status }}（{{ schedule.data.value.calendar_year }} 年）</b></div>
        <div v-if="staggerInfo.length" class="row"><span>错峰信息</span><b>{{ staggerInfo }}</b></div>
      </div>
      <p v-if="schedule.data.value.calendar_warning" class="feedback" role="alert">{{ schedule.data.value.calendar_warning }}</p>
      <p v-else class="subtle section-space">后端暂未提供错峰 claim 详情接口；当前展示调度器运行状态，轮询已做退避+抖动以减少早间窗口的集中刷新积压。</p>
    </div>
    <div class="panel table-panel section-space">
      <div class="panel-head"><div><h2>近期调度认领</h2><p>advisor_schedule_runs 最近记录</p></div></div>
      <DataTable table-id="report-schedule-runs" label="调度认领记录" :headers="['日期', '窗口', '状态', '开始时间', '报告', '错误码']" :rows="runRows" :min-width="760" empty-title="暂无调度记录" :sortable="false" />
    </div>
    <div class="panel table-panel section-space">
      <div class="panel-head"><div><h2>报告任务</h2><p>进行中的任务会高亮显示</p></div><span class="tag blue">今日用量 {{ reports.data.value?.usage.used ?? '—' }} / {{ reports.data.value?.usage.limit ?? '—' }}</span></div>
      <DataTable table-id="report-tasks" label="报告任务" :headers="['报告', '类型', '状态', '创建时间', '更新时间', '错误码']" :rows="reportRows" :min-width="820" empty-title="暂无报告任务" :sortable="false">
        <template #cell-2="{ value }"><span class="tag" :class="{ green: value === '已完成', red: value === '失败' || value === '已中断', blue: value !== '已完成' && value !== '失败' && value !== '已中断' }">{{ value }}</span></template>
      </DataTable>
    </div>
  </template>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import DataTable from './DataTable.vue'
import { useAccountResource } from '../composables/useAccountResource'

type ScheduleRun = { run_date: string; kind: string; status: string; started_at: string | null; report_id: string | null; error_code: string | null }
type Schedule = {
  running: boolean; timezone: string; trading_day: boolean | null; calendar_status: string; calendar_year: number
  calendar_warning: string | null; schedules?: { morning?: string; afternoon?: string }; items: ScheduleRun[]
  stagger_seconds?: number; claim_window?: string; claim_order?: string; next_claim_at?: string; stagger?: string
}
type Report = { report_id: string; kind: string; status: string; created_at: string; updated_at: string; error_code: string | null }
type ReportList = { items: Report[]; active_reports: { report_id: string; kind: string; status: string }[]; usage: { used: number; limit: number } }

const schedule = useAccountResource('report-schedule', (api, signal) => api.read<Schedule>('/advisor/schedule', signal))
const reports = useAccountResource('report-list', (api, signal) => api.read<ReportList>('/advisor/reports?limit=10', signal))
const session = schedule.session

const statusLabel: Record<string, string> = { queued: '排队中', running: '生成中', claimed: '已认领', completed: '已完成', failed: '失败', interrupted: '已中断' }
const labelOf = (status: string) => statusLabel[status] ?? status
const staggerInfo = computed(() => {
  const data = schedule.data.value
  if (!data) return ''
  const parts: string[] = []
  if (data.stagger_seconds !== undefined) parts.push(`错峰 ${data.stagger_seconds} 秒`)
  if (data.claim_window) parts.push(`认领窗口 ${data.claim_window}`)
  if (data.claim_order) parts.push(`认领顺序 ${data.claim_order}`)
  if (data.next_claim_at) parts.push(`下次认领 ${data.next_claim_at}`)
  if (data.stagger) parts.push(String(data.stagger))
  return parts.join(' · ')
})
const runRows = computed(() => (schedule.data.value?.items ?? []).map(item => [item.run_date, item.kind, labelOf(item.status), item.started_at ?? '—', item.report_id ?? '—', item.error_code ?? '—']))
const reportRows = computed(() => (reports.data.value?.items ?? []).map(item => [item.report_id, item.kind, labelOf(item.status), item.created_at, item.updated_at, item.error_code ?? '—']))

/** F12: 带退避与抖动的轮询。早间窗口 30 人同时刷新会造成积压，故：
 *  - 首次加载随机延迟 0–5 秒，错开齐步刷新；
 *  - 有进行中任务时按 5s→10s→20s→40s→60s 指数退避，状态无变化才加轮次；
 *  - 每次等待叠加 ±25% 抖动；无进行中任务时降为 180 秒低频。 */
const autoRefresh = ref(true), nextIn = ref(0), backoffRound = ref(0), polling = ref(false)
let countdown = Math.round(Math.random() * 5)
let lastSignature = ''
nextIn.value = countdown
const activeStatuses = ['queued', 'running', 'claimed']
const hasActive = () => (reports.data.value?.active_reports.length ?? 0) > 0 ||
  (schedule.data.value?.items ?? []).some(item => activeStatuses.includes(item.status))
const signature = () => JSON.stringify([(reports.data.value?.active_reports ?? []).map(r => r.report_id + r.status), (schedule.data.value?.items ?? []).map(i => i.run_date + i.kind + i.status)])
async function doPoll() {
  if (polling.value) return
  polling.value = true
  try {
    await Promise.all([schedule.refresh(), reports.refresh()])
    const sig = signature()
    if (sig !== lastSignature) { backoffRound.value = 0; lastSignature = sig }
    else if (hasActive()) backoffRound.value++
    const base = hasActive() ? Math.min(60, 5 * 2 ** Math.min(backoffRound.value, 4)) : 180
    countdown = Math.max(1, Math.round(base * (0.75 + Math.random() * 0.5)))
    nextIn.value = countdown
  } finally { polling.value = false }
}
async function manualRefresh() { backoffRound.value = 0; await doPoll() }
const ticker = window.setInterval(() => {
  if (!autoRefresh.value) return
  if (countdown > 0) { countdown--; nextIn.value = countdown; return }
  void doPoll()
}, 1000)
onBeforeUnmount(() => window.clearInterval(ticker))
</script>
