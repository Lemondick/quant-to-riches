<template>
  <PageHeader title="再平衡" subtitle="设定目标配置，监控实际偏离并获取调仓建议。" />
  <div class="page-context"><span class="data-badge">{{ demo ? '演示数据' : '账户数据' }}</span><span>本页仅展示调仓建议，不提供下单入口；实际交易请前往交易账本记录。</span></div>
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>

  <div class="two-col">
    <div class="panel form-panel">
      <div class="card-title">目标配置</div>
      <p class="subtle">股 / 债 / 现金比例之和须为 100%。</p>
      <div class="form-row"><label :for="id('equity')">股票类目标 %</label><input :id="id('equity')" v-model.number="draft.equity_pct" type="number" min="0" max="100" step="0.5" class="num-input" /></div>
      <div class="form-row"><label :for="id('bond')">债券类目标 %</label><input :id="id('bond')" v-model.number="draft.bond_pct" type="number" min="0" max="100" step="0.5" class="num-input" /></div>
      <div class="form-row"><label :for="id('cash')">现金类目标 %</label><input :id="id('cash')" v-model.number="draft.cash_pct" type="number" min="0" max="100" step="0.5" class="num-input" /></div>
      <div class="form-row"><label :for="id('industry')">行业集中度上限 %</label><input :id="id('industry')" v-model.number="draft.max_industry_pct" type="number" min="1" max="100" step="1" class="num-input" /></div>
      <div class="form-row"><label :for="id('threshold')">偏离阈值 %</label><input :id="id('threshold')" v-model.number="draft.deviation_threshold_pct" type="number" min="0.5" max="50" step="0.5" class="num-input" /></div>
      <div class="inline-actions">
        <button class="btn primary" :disabled="saving" @click="onSave">{{ saving ? '保存中' : '保存目标配置' }}</button>
        <button class="btn ghost" :disabled="saving" @click="resetDraft">恢复</button>
      </div>
    </div>

    <div class="panel card-pad">
      <div class="card-title">实际 vs 目标</div>
      <p class="subtle">截至 {{ state.as_of ?? '未知' }} · 总资产 ¥{{ totalValue }}</p>
      <div v-for="advice in adviceResult.advices" :key="advice.class" class="alloc-compare">
        <div class="alloc-head"><span>{{ advice.name }}</span><span class="mono">实际 {{ advice.currentPct.toFixed(1) }}% / 目标 {{ advice.targetPct.toFixed(1) }}%</span></div>
        <div class="bar-track"><div class="bar target" :style="{ width: advice.targetPct + '%' }"></div></div>
        <div class="bar-track"><div class="bar actual" :class="{ alert: advice.alert }" :style="{ width: Math.min(100, advice.currentPct) + '%' }"></div></div>
        <div class="alloc-diff" :class="{ alert: advice.alert }">偏离 {{ advice.diffPct >= 0 ? '+' : '' }}{{ advice.diffPct.toFixed(1) }}%<span v-if="advice.alert" class="tag red">超阈值</span></div>
      </div>
    </div>
  </div>

  <div v-if="adviceResult.hasAlert" class="alert-banner" role="alert">
    <TriangleAlert :size="16" aria-hidden="true" />
    <span>组合已偏离目标配置（阈值 ±{{ liveTarget.deviation_threshold_pct }}%），请查看下方调仓建议。</span>
  </div>
  <div v-else class="ok-banner" role="status">
    <CircleCheck :size="16" aria-hidden="true" />
    <span>当前配置在偏离阈值范围内，无需调仓。</span>
  </div>

  <div class="two-col">
    <div class="panel card-pad">
      <div class="card-title">调仓建议</div>
      <p class="subtle">仅建议方向与金额，不下单。</p>
      <div class="row-list">
        <div v-for="advice in adviceResult.advices" :key="advice.class" class="row">
          <span>{{ advice.name }}</span>
          <b v-if="advice.suggestion" class="suggestion">{{ advice.suggestion }}</b>
          <span v-else class="subtle">无需调整</span>
        </div>
      </div>
    </div>
    <div class="panel card-pad">
      <div class="card-title">行业集中度</div>
      <p class="subtle">上限 {{ liveTarget.max_industry_pct }}%</p>
      <div class="row-list">
        <div v-for="item in state.current.industries" :key="item.name" class="row">
          <span>{{ item.name }}</span>
          <b :class="{ 'text-danger': Number(item.pct) > liveTarget.max_industry_pct }">{{ Number(item.pct).toFixed(1) }}%</b>
        </div>
      </div>
      <p v-if="adviceResult.industryAlerts.length" class="feedback error" role="alert">
        {{ adviceResult.industryAlerts.map(a => `${a.name} ${a.pct.toFixed(1)}%`).join('、') }} 超过行业集中度上限。
      </p>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { CircleCheck, TriangleAlert } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import { buildRebalanceAdvice, validateRebalanceTarget, type RebalanceState, type RebalanceTarget } from '../services/batch2Api'

const props = withDefaults(defineProps<{
  state: RebalanceState
  demo?: boolean
  saving?: boolean
  notice?: string
}>(), { demo: false, saving: false, notice: '' })
const emit = defineEmits<{ save: [target: RebalanceTarget] }>()

const uid = Math.random().toString(36).slice(2)
const id = (name: string) => `reb-${uid}-${name}`
const draft = reactive<RebalanceTarget>({ ...props.state.target })
const formError = ref('')
watch(() => props.state.target, value => Object.assign(draft, value))
function resetDraft() { Object.assign(draft, props.state.target); formError.value = '' }

const liveTarget = computed(() => ({ ...props.state.target, ...draft }))
const adviceResult = computed(() => buildRebalanceAdvice({ ...props.state, target: liveTarget.value }))
const totalValue = computed(() => Math.round(Number(props.state.current.total_value) || 0).toLocaleString('zh-CN'))

function onSave() {
  formError.value = validateRebalanceTarget(draft) ?? ''
  if (formError.value) return
  emit('save', { ...draft })
}
</script>
<style scoped>
.card-title{font-size:14px;font-weight:700;margin-bottom:4px}
.subtle{font-size:11px;color:var(--text-2);margin:0 0 8px}
.num-input{height:34px;border:1px solid rgba(43,66,101,.7);border-radius:8px;background:rgba(20,37,62,.7);padding:0 10px;color:var(--text-1);font-size:13px;width:140px}
.mono{font-variant-numeric:tabular-nums;color:var(--text-2);font-size:11px}
.alloc-compare{margin:12px 0}
.alloc-head{display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px}
.bar-track{height:8px;border-radius:4px;background:rgba(34,51,77,.6);margin-bottom:4px;overflow:hidden}
.bar{height:100%;border-radius:4px}
.bar.target{background:rgba(129,146,171,.55)}
.bar.actual{background:linear-gradient(90deg,#3178f5,#5b82f0)}
.bar.actual.alert{background:linear-gradient(90deg,#e8a33d,#e05d5d)}
.alloc-diff{font-size:11px;color:var(--text-2)}
.alloc-diff.alert{color:#ffb86b}
.suggestion{font-size:12px}
.text-danger{color:#ff6d7d}
.alert-banner,.ok-banner{display:flex;align-items:center;gap:8px;padding:12px 16px;border-radius:10px;margin:12px 0;font-size:13px}
.alert-banner{background:rgba(68,38,53,.7);border:1px solid rgba(224,93,93,.5);color:#ffb3bb}
.ok-banner{background:rgba(24,60,51,.55);border:1px solid rgba(46,160,90,.4);color:#8fe3ae}
.inline-actions{display:flex;gap:8px;margin-top:12px}
</style>
