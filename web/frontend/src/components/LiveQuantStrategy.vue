<template>
  <PageHeader title="量化策略" subtitle="整理策略参数、查看结果，保留每次研究的记录。"><button class="btn" :disabled="refreshing" @click="refresh()">刷新策略</button><button class="btn primary" @click="formOpen = true"><Plus :size="15" />编辑策略</button></PageHeader>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p><p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <template v-if="data"><div class="page-context"><span class="data-badge">账户策略</span><span>只展示实际预览证据，缺少回测时不填充收益。</span></div><div class="cards-list strategy-cards"><article class="panel card-pad"><div class="detail-title"><div><h2 class="card-title">当前账户策略</h2><p class="subtle">已保存的研究参数</p></div><span class="tag blue">研究策略</span></div><div class="strategy-metrics"><div><span class="subtle">区间收益</span><strong>—</strong></div><div><span class="subtle">最大回撤</span><strong>—</strong></div><div><span class="subtle">夏普</span><strong>—</strong></div></div><div class="strategy-config"><span>候选 {{ data.parameters.top_candidate_count ?? '—' }} 个</span><span>最多持仓 {{ data.parameters.max_names ?? '—' }} 个</span></div><div class="inline-actions strategy-actions"><button class="btn" @click="formOpen = true">编辑参数</button><button class="text-action" :disabled="mutating" @click="preview()">预览候选与证据 <ArrowUpRight :size="13" /></button></div></article></div></template>
  <AppDialog v-model="formOpen" title="编辑策略参数"><form v-if="data" class="stack-form" aria-label="账户策略参数" @submit.prevent="save"><h2>策略参数</h2><p class="subtle">使用当前账户已保存的参数。保存与预览均由后端校验；缺少行情或历史证据时会列出阻断原因。</p><div class="form-grid"><label v-for="field in fields" :key="field.key">{{ field.label }}<input :value="values[field.key]" @input="updateParameter(field.key, $event)" class="field" type="number" :step="field.step" :min="field.min" :max="field.max" /></label></div><details><summary>高级参数</summary><label>参数配置<textarea v-model="parameters" class="field" rows="8" spellcheck="false" required /></label></details><div class="inline-actions"><button class="btn primary" :disabled="mutating" type="submit">保存参数</button><button class="btn" :disabled="mutating" type="button" @click="preview()">预览候选与证据</button></div></form><template #footer><button class="btn" @click="formOpen = false">关闭</button></template></AppDialog>
  <details v-if="result" class="panel card-pad section-space" open><summary>预览与来源证据</summary><pre class="break-anywhere" style="white-space:pre-wrap">{{ JSON.stringify(result, null, 2) }}</pre></details>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Plus, ArrowUpRight } from 'lucide-vue-next'
import AppDialog from './AppDialog.vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
const { data, error, refreshing, refresh, session, mutate, mutating } = useAccountResource('strategy-settings', (api, signal) => api.read<{ parameters: Record<string, unknown> }>('/research/strategy-settings', signal))
const formOpen = ref(false)
const fields = [{ key: 'top_candidate_count', label: '候选数量', step: 1, min: 1, max: 30 }, { key: 'max_names', label: '最多持仓数', step: 1, min: 1, max: 10 }, { key: 'target_weight', label: '单标的目标权重', step: 0.01, min: 0.01, max: 0.2 }, { key: 'exit_percentile', label: '退出分位', step: 0.01, min: 0.01, max: 1 }, { key: 'min_amount_20', label: '20 日最低成交额', step: 1000000, min: 10000000, max: 10000000000 }]
const parameters = ref(''), actionError = ref(''), notice = ref(''), result = ref<unknown>()
const values = computed<Record<string, number>>(() => { try { return JSON.parse(parameters.value) } catch { return {} } })
function updateParameter(key: string, event: Event) { parameters.value = JSON.stringify({ ...values.value, [key]: Number((event.target as HTMLInputElement).value) }, null, 2) }
watch(data, value => { if (value && !parameters.value) parameters.value = JSON.stringify(value.parameters ?? {}, null, 2) })
watch(() => session.value?.cache_scope, () => { parameters.value = ''; result.value = undefined; notice.value = ''; actionError.value = '' })
function body() { const value = JSON.parse(parameters.value); if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('策略参数必须为配置对象。'); return value }
async function save() { actionError.value = ''; try { await mutate('/research/strategy-settings', body(), 'PUT'); notice.value = '策略参数已保存。' } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '参数保存失败。' } }
async function preview() { actionError.value = ''; try { result.value = await mutate('/research/strategy-settings/preview', body()); notice.value = '预览已完成，请核对候选与阻断原因。' } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '策略预览暂不可用。' } }
</script>
