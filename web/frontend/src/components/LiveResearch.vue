<template>
  <PageHeader title="AI选股" subtitle="将研究想法转成可检查的条件，理解每个候选为什么入选。"><button class="btn" :disabled="refreshing" @click="refresh()">刷新研究</button><a class="btn primary" href="#native-research-form"><Plus :size="15" />新建研究</a></PageHeader>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p><p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <template v-if="data">
    <div class="page-context"><span class="data-badge">账户研究</span><span>根据已核验计划执行；提交前核对模型与预算。</span></div>
    <div class="ai-hero"><h2>AI 研究助手</h2><p>选择已核验的研究计划，检查候选与来源，再开始研究。</p><div class="research-query"><Sparkles :size="19" /><input :value="symbol" placeholder="先选择下方研究计划与候选标的" aria-label="研究目标" readonly /><a class="btn primary" href="#native-research-form">新建研究 <ArrowRight :size="14" /></a></div></div>
    <div class="two-col research-layout"><div class="panel card-pad">
    <form id="native-research-form" class="stack-form" aria-label="创建账户研究任务" @submit.prevent="create"><h2>新建研究</h2><p class="subtle">执行前由服务器重新核验来源、模型配置与预算。研究可能产生模型费用。</p>
      <label>已核验计划<select v-model="selected" class="field" required><option value="">请选择研究计划</option><option v-for="item in data.previews.items" :key="item.snapshot_id" :value="item.snapshot_id">{{ item.created_at }} · {{ (item.candidates ?? []).length }} 个候选</option></select></label>
      <label>研究标的<select v-model="symbol" class="field" required><option value="">请选择候选标的</option><option v-for="item in candidates" :key="item" :value="item">{{ item }}</option></select></label><button class="btn primary" type="submit" :disabled="mutating || !symbol || !selected">开始研究</button>
      <p v-if="!data.previews.items.length" class="feedback">暂无有效计划。<RouterLink to="/quant" class="text-action">前往量化策略完成预览</RouterLink></p>
    </form></div><div class="panel card-pad"><h2 class="card-title">最近研究</h2><p class="subtle">当前账户的研究任务与来源证据。</p><div v-if="data.jobs.items.length" class="activity-list"><button v-for="item in data.jobs.items.slice(0, 3)" :key="item.job_id" class="activity-button" @click="detail(item.job_id)"><b>{{ item.symbol }}</b><small>{{ item.created_at }} · {{ item.status }}</small></button></div><div v-else class="compact-empty"><History :size="26" /><p>完成第一次研究后，在这里继续查看。</p></div></div></div>
    <div class="panel table-panel section-space"><div class="panel-head"><h2>账户研究任务</h2></div><DataTable table-id="native-research-jobs" label="账户研究任务" :headers="['标的', '状态', '创建时间', '错误状态', '操作']" :rows="data.jobs.items.map(item => [item.symbol, item.status, item.created_at, item.error_code ?? '—', item.job_id])" empty-title="当前账户暂无研究任务"><template #cell-4="{ value }"><button class="text-action" @click="detail(String(value))">查看结果</button><button class="text-action" :disabled="mutating" @click="cancel(String(value))">取消任务</button></template></DataTable></div>
    <details v-if="report" class="panel card-pad section-space" open><summary>研究结果与证据</summary><pre class="break-anywhere" style="white-space:pre-wrap">{{ JSON.stringify(report, null, 2) }}</pre></details>
  </template>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Plus, Sparkles, ArrowRight, History } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import DataTable from './DataTable.vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
type Job = { job_id: string; symbol: string; status: string; error_code: string | null; created_at: string }
type Plan = { snapshot_id: string; created_at: string; candidates: string[]; blockers: unknown[] }
const { data, error, refreshing, refresh, session, read, mutate, mutating } = useAccountResource('research-jobs', async (api, signal) => {
  const [jobs, previews] = await Promise.all([api.read<{ items: Job[] }>('/research/agent-runs', signal), api.read<{ items: Plan[] }>('/research/previews', signal)])
  return { jobs, previews }
})
const selected = ref(''), symbol = ref(''), actionError = ref(''), notice = ref(''), report = ref<unknown>()
const candidates = computed(() => data.value?.previews.items.find(item => item.snapshot_id === selected.value)?.candidates ?? [])
watch(selected, () => { symbol.value = '' })
watch(() => session.value?.cache_scope, () => { selected.value = ''; symbol.value = ''; report.value = undefined; actionError.value = '' })
async function create() { actionError.value = ''; try { await mutate('/research/agent-runs', { preview_snapshot_id: selected.value, symbol: symbol.value }); notice.value = '研究任务已提交。' } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '研究未能提交。' } }
async function detail(id: string) { try { report.value = await read(`/research/agent-runs/${encodeURIComponent(id)}`) } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '研究暂不可用。' } }
async function cancel(id: string) { try { await mutate(`/research/agent-runs/${encodeURIComponent(id)}/cancel`, {}); notice.value = '已提交取消请求。' } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '取消失败。' } }
</script>
