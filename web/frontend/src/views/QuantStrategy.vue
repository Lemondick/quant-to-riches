<template>
  <LiveQuantStrategy v-if="liveBackend" />
  <template v-else>
  <PageHeader title="量化策略" subtitle="整理策略参数、查看示例结果，保留每次研究的记录。"><button class="btn" @click="historyOpen = true"><History :size="15" aria-hidden="true" />回测记录 · {{ state.logs.length }}</button><button class="btn primary" @click="editStrategy()"><Plus :size="15" aria-hidden="true" />新建策略</button></PageHeader>
  <div class="page-context"><span class="data-badge">本地策略演示</span><span>结果来自预置样本与演示费用假设，不执行真实回测或交易。</span></div>
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <div class="cards-list strategy-cards">
    <article v-for="strategy in state.strategies" :key="strategy.id" class="panel card-pad">
      <div class="detail-title"><div><h2 class="card-title">{{ strategy.name }}</h2><p class="subtle">{{ templateName(strategy.template) }}</p></div><span class="tag" :class="{ blue: strategy.status === '暂停' }">{{ strategy.status === '运行中' ? '已启用' : '已暂停' }}</span></div>
      <div class="strategy-metrics"><div><span class="subtle">样本区间收益</span><strong :class="(strategy.result?.ret ?? 0) >= 0 ? 'up' : 'down'">{{ strategy.result ? percent(strategy.result.ret) : '—' }}</strong></div><div><span class="subtle">样本最大回撤</span><strong class="down">{{ strategy.result ? percent(strategy.result.dd) : '—' }}</strong></div><div><span class="subtle">样本夏普</span><strong>{{ strategy.result?.sharpe.toFixed(2) ?? '—' }}</strong></div></div>
      <div class="strategy-config"><span>{{ strategy.period }}</span><span>单边费用：万分之 {{ strategy.feeBps }}</span></div>
      <div class="inline-actions strategy-actions"><button class="btn" :aria-label="'编辑策略：' + strategy.name" @click="editStrategy(strategy)">编辑参数</button><button class="btn" :aria-label="(strategy.status === '运行中' ? '暂停策略：' : '启用策略：') + strategy.name" @click="toggleStrategy(strategy)">{{ strategy.status === '运行中' ? '暂停' : '启用' }}</button><button class="text-action" :aria-label="'查看示例结果：' + strategy.name" @click="showResult(strategy)">查看示例结果 <ArrowUpRight :size="13" aria-hidden="true" /></button></div>
    </article>
  </div>
  <div class="panel table-panel section-space"><div class="panel-head"><div><h2>示例结果记录</h2><p>记录实际查看过的示例参数，不代表真实策略执行。</p></div><button class="btn" :disabled="!state.logs.length" @click="exportLogs"><Download :size="14" aria-hidden="true" />导出记录</button></div><DataTable table-id="strategy-log" label="策略运行日志" :headers="['策略', '记录时间', '区间', '样本收益', '样本回撤', '样本交易次数']" :rows="logRows" :numeric-columns="[3, 4, 5]" empty-title="尚无示例结果记录" empty-text="从上方策略选择“查看示例结果”开始。" /></div>
  <AppDialog v-model="formOpen" :title="editingId ? '编辑策略参数' : '新建策略'">
    <form id="strategy-form" class="stack-form" @submit.prevent="saveStrategy"><label>策略名称<input v-model.trim="draft.name" class="field" required maxlength="30" /></label><label>研究模板<select v-model="draft.template" class="field" aria-label="研究模板"><option v-for="template in strategyTemplates" :key="template.id" :value="template.id">{{ template.name }}</option></select></label><div class="form-grid"><label>示例区间<select v-model="draft.period" class="field" aria-label="示例区间"><option v-for="period in strategyPeriods" :key="period">{{ period }}</option></select></label><label>单边费用（万分比）<input v-model.number="draft.feeBps" class="field" type="number" min="0" max="100" step="0.1" required /></label></div><p class="form-hint">保存后可查看此配置的示例结果。参数变更会清除旧结果，历史记录继续保留。</p><p v-if="error" class="feedback error" role="alert">{{ error }}</p></form>
    <template #footer><button class="btn" @click="formOpen = false">取消</button><button class="btn primary" type="submit" form="strategy-form">保存策略</button></template>
  </AppDialog>
  <AppDialog v-model="resultOpen" :title="currentLog ? currentLog.name + ' · 示例结果' : '示例结果'" drawer description="预置样本仅用于演示配置与结果展示，不能替代使用真实行情的回测。">
    <template v-if="currentLog"><div class="row-list"><div class="row"><span>示例区间</span><b>{{ currentLog.period }}</b></div><div class="row"><span>单边费用</span><b>万分之 {{ currentLog.feeBps }}</b></div><div class="row"><span>样本区间收益</span><b class="up">{{ percent(currentLog.result.ret) }}</b></div><div class="row"><span>样本最大回撤</span><b class="down">{{ percent(currentLog.result.dd) }}</b></div><div class="row"><span>样本交易次数</span><b>{{ currentLog.result.tradeCount }} 次</b></div></div><p class="form-hint section-space">费用演示按样本交易次数扣减，不包含冲击成本、税费、流动性约束或真实成交模拟。</p></template>
    <template #footer><button class="btn" @click="resultOpen = false">关闭</button><button class="btn primary" @click="exportLogs">导出示例记录</button></template>
  </AppDialog>
  <AppDialog v-model="historyOpen" title="回测示例记录" drawer><div v-if="state.logs.length" class="activity-list"><button v-for="log in state.logs" :key="log.id" class="activity-button" @click="currentLog = log; historyOpen = false; resultOpen = true"><b>{{ log.name }}</b><small>{{ dateTime(log.date) }} · {{ log.period }} · {{ percent(log.result.ret) }}</small></button></div><div v-else class="compact-empty">暂无记录，先选择一项策略查看示例结果。</div></AppDialog>
</template>
</template>
<script setup lang="ts">
import LiveQuantStrategy from '../components/LiveQuantStrategy.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
import { computed, reactive, ref } from 'vue'
import { ArrowUpRight, Download, History, Plus } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import AppDialog from '../components/AppDialog.vue'
import { useLocalState } from '../composables/useLocalState'
import { strategyTemplates, strategyPeriods, initialStrategies, demoStrategyResult, type Strategy, type StrategyLog } from '../data/strategies'
import { dateTime, percent, downloadCsv } from '../utils/format'
type StrategyState = { strategies: Strategy[]; logs: StrategyLog[] }
const state = useLocalState<StrategyState>('guanlan.strategies.v1', { strategies: initialStrategies, logs: [] }, (value): value is StrategyState => !!value && typeof value === 'object' && Array.isArray((value as StrategyState).strategies) && (value as StrategyState).strategies.every(strategy => typeof strategy.id === 'string' && typeof strategy.name === 'string' && strategyTemplates.some(template => template.id === strategy.template) && strategyPeriods.includes(strategy.period) && Number.isFinite(strategy.feeBps) && ['运行中', '暂停'].includes(strategy.status) && (!strategy.result || [strategy.result.ret, strategy.result.dd, strategy.result.sharpe, strategy.result.tradeCount].every(Number.isFinite))) && Array.isArray((value as StrategyState).logs) && (value as StrategyState).logs.every(log => typeof log.id === 'string' && typeof log.name === 'string' && typeof log.date === 'string' && !!log.result && [log.result.ret, log.result.dd, log.result.sharpe, log.result.tradeCount].every(Number.isFinite)))
const notice = ref(''), formOpen = ref(false), error = ref(''), editingId = ref(''), resultOpen = ref(false), historyOpen = ref(false), currentLog = ref<StrategyLog | null>(null)
const draft = reactive({ name: '', template: 'value', period: '近1年', feeBps: 3 })
const templateName = (id: string) => strategyTemplates.find(template => template.id === id)?.description ?? ''
const logRows = computed(() => state.value.logs.map(log => [log.name, dateTime(log.date), log.period, percent(log.result.ret), percent(log.result.dd), log.result.tradeCount]))
function editStrategy(strategy?: Strategy) { editingId.value = strategy?.id ?? ''; Object.assign(draft, strategy ?? { name: '', template: 'value', period: '近1年', feeBps: 3 }); error.value = ''; formOpen.value = true }
function saveStrategy() {
  try {
    if (!draft.name.trim()) throw new Error('请输入策略名称。')
    if (state.value.strategies.some(item => item.name === draft.name.trim() && item.id !== editingId.value)) throw new Error('策略名称已存在，请使用其他名称。')
    demoStrategyResult(draft.template, draft.period, draft.feeBps)
    const current = state.value.strategies.find(item => item.id === editingId.value)
    if (current) { Object.assign(current, { name: draft.name.trim(), template: draft.template, period: draft.period, feeBps: draft.feeBps, result: null }) }
    else state.value.strategies.push({ id: crypto.randomUUID(), name: draft.name.trim(), template: draft.template, period: draft.period, feeBps: draft.feeBps, status: '暂停', result: null })
    formOpen.value = false; notice.value = '策略配置已保存，可查看当前参数的示例结果。'
  } catch (exception) { error.value = (exception as Error).message }
}
function toggleStrategy(strategy: Strategy) { strategy.status = strategy.status === '运行中' ? '暂停' : '运行中'; notice.value = '已更新本地演示状态，不会启动真实交易任务。' }
function showResult(strategy: Strategy) { const result = demoStrategyResult(strategy.template, strategy.period, strategy.feeBps); strategy.result = result; const log: StrategyLog = { id: crypto.randomUUID(), name: strategy.name, date: new Date().toISOString(), period: strategy.period, feeBps: strategy.feeBps, result }; state.value.logs.unshift(log); state.value.logs = state.value.logs.slice(0, 30); currentLog.value = log; resultOpen.value = true }
function exportLogs() { downloadCsv('观澜-策略示例记录.csv', ['策略', '时间', '区间', '样本收益', '样本回撤', '样本交易次数'], logRows.value) }
</script>
