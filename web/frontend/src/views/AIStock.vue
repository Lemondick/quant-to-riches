<template>
  <LiveResearch v-if="liveBackend" />
  <template v-else>
  <PageHeader title="AI选股" subtitle="将研究想法转成可检查的条件，理解每个候选为什么入选。"><button class="btn" @click="historyOpen = true"><History :size="15" aria-hidden="true" />历史任务 · {{ tasks.length }}</button><button class="btn primary" @click="newTask"><Plus :size="15" aria-hidden="true" />新建研究</button></PageHeader>
  <div class="page-context"><span class="data-badge">本地规则演示</span><span>使用虚构指标样本演示筛选流程，未连接在线模型或真实行情。</span></div>
  <div class="ai-hero">
    <h2>AI 研究助手</h2><p>描述研究方向，或直接勾选条件。识别结果可修改，每个候选都会展示对应指标。</p>
    <form class="research-query" @submit.prevent="runResearch"><Sparkles :size="19" aria-hidden="true" /><input ref="queryInput" v-model="query" aria-label="研究目标" placeholder="例如：寻找低估值、高股息且盈利改善的标的" maxlength="200" @input="criteria = inferCriteria(query)" /><button class="btn primary" type="submit">开始分析 <ArrowRight :size="14" aria-hidden="true" /></button></form>
    <div class="preset-prompts"><button v-for="preset in presets" :key="preset" type="button" @click="choosePreset(preset)">{{ preset }}</button></div>
  </div>
  <div class="two-col research-layout">
    <div class="panel card-pad"><div class="detail-title"><div><h2 class="card-title">筛选条件</h2><p class="subtle">可多选，候选需要同时满足所有已选条件。</p></div><span class="tag blue">{{ criteria.length }} 项条件</span></div>
      <div class="criteria-list"><label v-for="condition in researchCriteria" :key="condition.id" class="criteria-option" :class="{ selected: criteria.includes(condition.id) }"><input v-model="criteria" type="checkbox" :value="condition.id" /><span><b>{{ condition.label }}</b><small>{{ condition.description }}</small></span><Check v-if="criteria.includes(condition.id)" :size="15" aria-hidden="true" /></label></div>
      <p v-if="error" class="feedback error" role="alert">{{ error }}</p><p v-else class="form-hint">当前演示支持以上四类条件，阈值展示在每项说明中。</p>
    </div>
    <div class="panel card-pad"><h2 class="card-title">最近研究</h2><p class="subtle">仅保存在本浏览器，保留最近 12 次任务。</p><div v-if="tasks.length" class="activity-list"><button v-for="task in tasks.slice(0, 3)" :key="task.id" class="activity-button" @click="restoreTask(task)"><span>{{ task.query || '自定义条件研究' }}</span><small>{{ dateTime(task.date) }} · {{ task.codes.length }} 个候选</small></button></div><div v-else class="compact-empty"><History :size="26" aria-hidden="true" /><p>完成第一次分析后，在这里继续研究。</p></div></div>
  </div>
  <div v-if="resultCodes !== null" class="panel table-panel section-space">
    <div class="panel-head"><div><h2>候选标的</h2><p>依据本次条件筛选，不构成买卖建议。</p></div><span class="tag blue">命中 {{ candidates.length }} / {{ stockSamples.length }} 个样本</span></div>
    <div class="applied-conditions"><span v-for="condition in researchCriteria.filter(item => appliedCriteria.includes(item.id))" :key="condition.id" class="filter-chip">{{ condition.label }}</span></div>
    <DataTable table-id="research-candidates" label="研究候选标的" :headers="['标的', '代码', '行业', '市盈率', '样本股息率', '近90日涨幅', '利润增速', '操作']" :rows="candidateRows" :numeric-columns="[3, 4, 5, 6]" :min-width="940" empty-title="没有同时满足条件的样本" empty-text="可减少条件，或修改研究方向后重新分析。"><template #cell-7="{ row }"><button class="text-action" :aria-label="'查看入选依据：' + row[0]" @click="detailCode = String(row[1]); detailOpen = true">查看依据</button></template></DataTable>
  </div>
  <AppDialog v-model="historyOpen" title="研究历史" drawer description="点击任务恢复当时的条件和候选结果。"><div v-if="tasks.length" class="activity-list"><button v-for="task in tasks" :key="task.id" class="activity-button" @click="restoreTask(task)"><b>{{ task.query || '自定义条件研究' }}</b><small>{{ dateTime(task.date) }} · {{ task.codes.length }} 个候选</small></button></div><div v-else class="compact-empty">暂无研究任务，先从推荐条件开始。</div></AppDialog>
  <AppDialog v-model="detailOpen" :title="detailStock ? detailStock.name + ' · 入选依据' : '入选依据'" drawer description="以下指标来自虚构的本地样本，用于展示可解释的筛选过程。">
    <template v-if="detailStock"><div class="article-meta">{{ detailStock.code }} · {{ detailStock.industry }}</div><div class="evidence-list"><div v-for="condition in researchCriteria.filter(item => appliedCriteria.includes(item.id))" :key="condition.id"><span class="tag">{{ condition.label }} · 已满足</span><p>{{ evidence(condition.id, detailStock) }}</p></div></div><p class="form-hint">请核实原始数据、统计口径和时间，再作任何真实研究判断。</p></template>
  </AppDialog>
</template>
</template>
<script setup lang="ts">
import LiveResearch from '../components/LiveResearch.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
import { computed, nextTick, ref } from 'vue'
import { ArrowRight, Check, History, Plus, Sparkles } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import AppDialog from '../components/AppDialog.vue'
import DataTable from '../components/DataTable.vue'
import { useLocalState } from '../composables/useLocalState'
import { researchCriteria, inferCriteria, screenStocks, stockSamples, type ResearchCriterion } from '../data/research'
import { dateTime, percent } from '../utils/format'
type ResearchTask = { id: string; date: string; query: string; criteria: ResearchCriterion[]; codes: string[] }
const tasks = useLocalState<ResearchTask[]>('guanlan.research-tasks.v1', [], (value): value is ResearchTask[] => Array.isArray(value) && value.every(task => task && typeof task.id === 'string' && typeof task.date === 'string' && !Number.isNaN(Date.parse(task.date)) && typeof task.query === 'string' && Array.isArray(task.criteria) && task.criteria.every((id: unknown) => researchCriteria.some(item => item.id === id)) && Array.isArray(task.codes) && task.codes.every((code: unknown) => stockSamples.some(item => item.code === code))))
const presets = ['低估值 + 盈利改善', '高股息 + 趋势改善', '低估值 + 高股息']
const query = ref(''), criteria = ref<ResearchCriterion[]>(['value', 'quality']), appliedCriteria = ref<ResearchCriterion[]>([]), resultCodes = ref<string[] | null>(null), error = ref(''), historyOpen = ref(false), detailOpen = ref(false), detailCode = ref(''), queryInput = ref<HTMLInputElement>()
const candidates = computed(() => stockSamples.filter(stock => resultCodes.value?.includes(stock.code)))
const candidateRows = computed(() => candidates.value.map(stock => [stock.name, stock.code, stock.industry, stock.pe.toFixed(1) + ' 倍', percent(stock.dividend, false), percent(stock.momentum), percent(stock.growth), stock.code]))
const detailStock = computed(() => stockSamples.find(stock => stock.code === detailCode.value))
function choosePreset(preset: string) { query.value = preset; criteria.value = inferCriteria(preset); error.value = '' }
function runResearch() {
  if (!criteria.value.length) { error.value = '请至少勾选一项条件；当前描述未识别到受支持的条件。'; return }
  error.value = ''; appliedCriteria.value = [...criteria.value]; resultCodes.value = screenStocks(criteria.value).map(stock => stock.code)
  tasks.value = [{ id: crypto.randomUUID(), date: new Date().toISOString(), query: query.value, criteria: [...criteria.value], codes: [...resultCodes.value] }, ...tasks.value].slice(0, 12)
}
function restoreTask(task: ResearchTask) { query.value = task.query; criteria.value = [...task.criteria]; appliedCriteria.value = [...task.criteria]; resultCodes.value = [...task.codes]; historyOpen.value = false; error.value = '' }
async function newTask() { query.value = ''; criteria.value = []; appliedCriteria.value = []; resultCodes.value = null; error.value = ''; await nextTick(); queryInput.value?.focus() }
function evidence(id: ResearchCriterion, stock: typeof stockSamples[number]) { return id === 'value' ? '样本市盈率 ' + stock.pe + ' 倍，满足不高于 20 倍。' : id === 'dividend' ? '样本股息率 ' + stock.dividend + '%，满足不低于 3%。' : id === 'momentum' ? '近90日样本涨幅 ' + percent(stock.momentum) + '，满足大于 0。' : '样本利润增速 ' + percent(stock.growth) + '，满足大于 0。' }
</script>
