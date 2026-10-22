<template>
  <PageHeader title="组合回测" subtitle="配置基金权重、再平衡规则与定投，回测历史表现。" />
  <div class="page-context"><span class="data-badge">{{ demo ? '演示回测' : '历史回测' }}</span><span>回测为历史模拟，不构成投资建议。</span></div>
  <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
  <p v-if="runError" class="feedback error" role="alert">{{ runError }}</p>

  <div class="panel form-panel">
    <div class="card-title">组合权重配置 <span class="subtle">权重之和须为 100%</span></div>
    <div v-for="(fund, index) in funds" :key="index" class="weight-row">
      <input v-model.trim="fund.code" placeholder="基金代码" class="field code" maxlength="12" aria-label="基金代码" />
      <input v-model.trim="fund.name" placeholder="名称（可选）" class="field" maxlength="40" aria-label="基金名称" />
      <input v-model.number="fund.weight" type="number" min="0" max="100" step="1" class="field weight" aria-label="权重百分比" />
      <span class="pct">%</span>
      <button class="text-action" :disabled="funds.length <= 1" @click="funds.splice(index, 1)">删除</button>
    </div>
    <button class="btn" @click="funds.push({ code: '', name: '', weight: 0 })"><Plus :size="14" aria-hidden="true" />添加基金</button>

    <div class="card-title section-space">回测区间</div>
    <div class="date-row">
      <label>开始<input v-model="start" type="date" class="field" /></label>
      <label>结束<input v-model="end" type="date" class="field" /></label>
    </div>

    <div class="card-title section-space">再平衡规则</div>
    <div class="radio-group" role="radiogroup" aria-label="再平衡规则">
      <label><input v-model="rebalanceMode" type="radio" value="period" />按周期</label>
      <label><input v-model="rebalanceMode" type="radio" value="threshold" />按偏离阈值</label>
    </div>
    <div v-if="rebalanceMode === 'period'" class="inline-field">
      <label>周期<select v-model="period" class="field"><option value="1M">每月（1M）</option><option value="3M">每季（3M）</option></select></label>
    </div>
    <div v-else class="inline-field">
      <label>偏离阈值 %<input v-model.number="thresholdPct" type="number" min="0.5" max="50" step="0.5" class="field narrow" /></label>
    </div>

    <div class="card-title section-space">定投现金流 <span class="subtle">可选</span></div>
    <label class="check"><input v-model="dcaEnabled" type="checkbox" />启用定投</label>
    <div v-if="dcaEnabled" class="date-row">
      <label>每期金额<input v-model.number="dcaAmount" type="number" min="1" step="100" class="field" /></label>
      <label>频率<select v-model="dcaFreq" class="field"><option value="monthly">每月</option><option value="weekly">每周</option></select></label>
    </div>

    <div class="inline-actions">
      <button class="btn primary" :disabled="running" @click="run">{{ running ? '回测中' : '开始回测' }}</button>
    </div>
  </div>

  <template v-if="result">
    <p v-for="warning in result.warnings ?? []" :key="warning" class="feedback">{{ warning }}</p>
    <div class="kpi-grid section-space">
      <div class="panel metric-card"><span class="metric-label">累计收益</span><div class="metric-value" :class="result.metrics.total_return_pct >= 0 ? 'up' : 'down'">{{ signed(result.metrics.total_return_pct) }}%</div><div class="metric-note">区间总回报</div></div>
      <div class="panel metric-card"><span class="metric-label">XIRR 年化</span><div class="metric-value" :class="result.metrics.xirr_pct !== null && result.metrics.xirr_pct >= 0 ? 'up' : 'down'">{{ result.metrics.xirr_pct === null ? '—' : signed(result.metrics.xirr_pct) + '%' }}</div><div class="metric-note">内部收益率</div></div>
      <div class="panel metric-card"><span class="metric-label">最大回撤</span><div class="metric-value down">{{ result.metrics.max_drawdown_pct.toFixed(2) }}%</div><div class="metric-note">区间最差跌幅</div></div>
      <div class="panel metric-card"><span class="metric-label">夏普比率</span><div class="metric-value">{{ result.metrics.sharpe ?? '—' }}</div><div class="metric-note">风险调整后收益</div></div>
    </div>
    <div v-if="result.metrics.total_invested !== null" class="row-list panel card-pad section-space">
      <div class="row"><span>累计投入（含定投）</span><b>¥{{ result.metrics.total_invested.toLocaleString('zh-CN') }}</b></div>
      <div class="row"><span>期末市值</span><b>¥{{ (result.metrics.final_value ?? 0).toLocaleString('zh-CN') }}</b></div>
    </div>
    <ChartCard title="组合净值曲线" subtitle="回测区间内组合净值相对涨跌幅" :observations="observations" :ranges="['回测区间']" range="回测区间" class="section-space" />
    <div class="panel card-pad section-space">
      <div class="card-title">再平衡事件时间线 <span class="subtle">{{ result.rebalance_events.length }} 次</span></div>
      <ol v-if="result.rebalance_events.length" class="timeline">
        <li v-for="(event, index) in result.rebalance_events" :key="index">
          <span class="t-date">{{ event.date }}</span>
          <span class="tag blue">{{ event.action }}</span>
          <span class="t-detail">{{ event.detail }}</span>
        </li>
      </ol>
      <p v-else class="subtle">回测区间内未触发再平衡。</p>
    </div>
  </template>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { Plus } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import ChartCard from './ChartCard.vue'
import {
  backtestCurveObservations, validateBacktestRequest,
  type BacktestRequest, type BacktestResult,
} from '../services/batch2Api'
import { mockBacktestFunds } from '../data/batch2'

const props = withDefaults(defineProps<{
  demo?: boolean
  runBacktest: (request: BacktestRequest) => Promise<BacktestResult>
  initialFunds?: { code: string; name: string; weight: number }[]
}>(), { demo: false, initialFunds: () => mockBacktestFunds() })

const today = new Date().toISOString().slice(0, 10)
const yearAgo = new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10)
const funds = ref(props.initialFunds.map(f => ({ ...f })))
const start = ref(yearAgo), end = ref(today)
const rebalanceMode = ref<'period' | 'threshold'>('period')
const period = ref<'1M' | '3M'>('3M')
const thresholdPct = ref(5)
const dcaEnabled = ref(false), dcaAmount = ref(1000), dcaFreq = ref<'monthly' | 'weekly'>('monthly')
const running = ref(false), formError = ref(''), runError = ref('')
const result = ref<BacktestResult | null>(null)
const observations = computed(() => result.value ? backtestCurveObservations(result.value) : undefined)

function signed(value: number): string { return (value >= 0 ? '+' : '') + value.toFixed(2) }

async function run() {
  formError.value = ''; runError.value = ''; result.value = null
  const request: BacktestRequest = {
    funds: funds.value.map(f => ({ code: f.code.trim(), name: f.name.trim() || undefined, weight: f.weight })),
    start: start.value, end: end.value,
    rebalance: rebalanceMode.value === 'period' ? { mode: 'period', period: period.value } : { mode: 'threshold', threshold_pct: thresholdPct.value },
    ...(dcaEnabled.value ? { dca: { amount: dcaAmount.value, freq: dcaFreq.value } } : {}),
  }
  formError.value = validateBacktestRequest(request) ?? ''
  if (formError.value) return
  running.value = true
  try {
    result.value = await props.runBacktest(request)
  } catch (failure) {
    runError.value = failure instanceof Error ? failure.message : '回测失败。'
  } finally {
    running.value = false
  }
}
</script>
<style scoped>
.card-title{font-size:14px;font-weight:700;margin-bottom:8px}
.subtle{font-size:11px;color:var(--text-2);font-weight:400}
.weight-row{display:grid;grid-template-columns:130px 1fr 90px 24px 60px;gap:8px;align-items:center;margin-bottom:8px}
.field{height:34px;border:1px solid rgba(43,66,101,.7);border-radius:8px;background:rgba(20,37,62,.7);padding:0 10px;color:var(--text-1);font-size:13px;width:100%}
.field.code{font-family:ui-monospace,monospace}
.field.narrow{width:110px}
.pct{color:var(--text-2);font-size:12px}
.date-row{display:flex;gap:12px;flex-wrap:wrap}
.date-row label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--text-2)}
.radio-group{display:flex;gap:16px;font-size:13px;margin:8px 0}
.radio-group label{display:flex;align-items:center;gap:6px}
.inline-field{margin:8px 0}
.inline-field label{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-2)}
.check{display:flex;align-items:center;gap:8px;font-size:13px;margin:8px 0}
.inline-actions{margin-top:14px}
.timeline{list-style:none;margin:8px 0 0;padding:0 0 0 4px}
.timeline li{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(34,51,77,.5);font-size:12px}
.timeline li:last-child{border-bottom:0}
.t-date{font-variant-numeric:tabular-nums;color:var(--text-2);min-width:96px}
.t-detail{color:var(--text-2)}
</style>
