<template>
  <PageHeader title="定投跟踪" subtitle="计划 vs 执行对比：按期、漏投、金额偏离。" />
  <BackendState :error="error" :refreshing="refreshing && !loadedOnce" :has-data="loadedOnce" @retry="load()" />
  <p v-if="mockNotice" class="feedback" role="status">{{ mockNotice }}</p>
  <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
  <DcaTrackingPanel v-if="tracking" :tracking="tracking" @register="dialogOpen = true" />

  <AppDialog v-model="dialogOpen" title="登记扣款" description="登记一次实际扣款（只做记录与提醒，不执行真实交易）。">
    <form class="stack-form" @submit.prevent="registerExecution">
      <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
      <p v-if="formNotice" class="feedback" role="status">{{ formNotice }}</p>
      <div class="form-grid">
        <label>扣款日期<input v-model="form.due_date" class="field" type="date" required /></label>
        <label>实际扣款金额（元）<input v-model.trim="form.actual_amount" class="field" inputmode="decimal" placeholder="3000" required /></label>
      </div>
      <label>备注（可选）<input v-model.trim="form.note" class="field" maxlength="120" placeholder="如：手动补投" /></label>
      <button class="btn primary" type="submit" :disabled="saving">{{ saving ? '登记中…' : '确认登记' }}</button>
    </form>
  </AppDialog>
</template>
<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import AppDialog from './AppDialog.vue'
import DcaTrackingPanel from './DcaTrackingPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import { validateExecutionForm, type DcaTracking } from '../services/batch5Api'

/** 后端接口（A 流并行开发）：GET api/v1/dca/tracking · POST api/v1/dca/executions；
 *  未就绪时用契约 mock 保证可构建、可预览（页面明确标注待联调）。 */
const { error, refreshing, session, read, mutate } = useAccountResource('dca-tracking',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const tracking = ref<DcaTracking | null>(null)
const loadedOnce = ref(false)
const loadError = ref('')
const mockNotice = ref('')

async function load() {
  loadError.value = ''
  try {
    tracking.value = await read<DcaTracking>('/dca/tracking')
    mockNotice.value = ''
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockDcaTracking } = await import('../data/batch5')
      tracking.value = mockDcaTracking()
      mockNotice.value = '后端定投跟踪接口尚未就绪（A 并行开发中），当前为契约演示数据，待联调后切换为真实数据。'
    } else {
      loadError.value = failure instanceof Error ? failure.message : '定投跟踪读取失败。'
    }
  }
  loadedOnce.value = true
}

/* ---- 登记扣款 ---- */
const dialogOpen = ref(false)
const saving = ref(false)
const formError = ref('')
const formNotice = ref('')
const form = reactive({ due_date: '', actual_amount: '', note: '' })

async function registerExecution() {
  formError.value = ''; formNotice.value = ''
  if (!tracking.value) return
  const payload = { plan_id: tracking.value.plan_id, due_date: form.due_date, actual_amount: form.actual_amount, note: form.note.trim() || undefined }
  const validation = validateExecutionForm(payload)
  if (validation) { formError.value = validation; return }
  saving.value = true
  try {
    await mutate('/dca/executions', { ...payload, actual_amount: Number(payload.actual_amount) }, 'POST')
    dialogOpen.value = false
    form.due_date = ''; form.actual_amount = ''; form.note = ''
    await load()
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404 && mockNotice.value) {
      // 演示模式：本地追加一条已扣款记录，保持页面可预览。
      const periods = tracking.value!.periods.map(period =>
        period.due_date === payload.due_date ? { ...period, status: 'done' as const, actual_amount: Number(payload.actual_amount), executed_at: new Date().toISOString(), note: payload.note ?? period.note } : period)
      if (!periods.some(period => period.due_date === payload.due_date)) {
        periods.push({ due_date: payload.due_date, status: 'done', planned_amount: periods.at(-1)?.planned_amount ?? 0, actual_amount: Number(payload.actual_amount), executed_at: new Date().toISOString(), note: payload.note ?? null })
      }
      tracking.value = { ...tracking.value!, periods }
      dialogOpen.value = false
      form.due_date = ''; form.actual_amount = ''; form.note = ''
      loadError.value = '演示模式：登记仅在本地预览生效，联调后将由后端持久化并记审计。'
    } else {
      formError.value = failure instanceof Error ? failure.message : '登记失败。'
    }
  } finally {
    saving.value = false
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
