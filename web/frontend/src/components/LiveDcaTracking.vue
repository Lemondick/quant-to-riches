<template>
  <PageHeader title="定投跟踪" subtitle="计划 vs 执行对比：按期、漏投、金额偏离。" />
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!plans" @retry="refresh()" />
  <label v-if="plans?.length" class="stack-form">定投计划<select v-model="selectedPlanId" class="field" aria-label="定投计划"><option v-for="plan in plans" :key="plan.plan_id" :value="plan.plan_id">{{ plan.plan_name }}</option></select></label>
  <DcaTrackingPanel v-if="tracking" :tracking="tracking" @register="dialogOpen = true" />
  <p v-else-if="plans" class="feedback" role="status">暂无可跟踪的定投计划。</p>

  <AppDialog v-model="dialogOpen" title="登记扣款" description="登记一次实际扣款（只做记录与提醒，不执行真实交易）。">
    <form class="stack-form" @submit.prevent="registerExecution">
      <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
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
import { computed, reactive, ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import AppDialog from './AppDialog.vue'
import DcaTrackingPanel from './DcaTrackingPanel.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadDcaTracking, validateExecutionForm } from '../services/batch5Api'

const { data: plans, error, refreshing, refresh, session, mutate } = useAccountResource('dca-tracking', loadDcaTracking)
const selectedPlanId = ref('')
const tracking = computed(() => plans.value?.find(plan => plan.plan_id === selectedPlanId.value))
watch(plans, value => { if (!value?.some(plan => plan.plan_id === selectedPlanId.value)) selectedPlanId.value = value?.[0]?.plan_id ?? '' })

/* ---- 登记扣款 ---- */
const dialogOpen = ref(false)
const saving = ref(false)
const formError = ref('')
const form = reactive({ due_date: '', actual_amount: '', note: '' })

async function registerExecution() {
  formError.value = ''
  if (!tracking.value) return
  const payload = { plan_id: tracking.value.plan_id, due_date: form.due_date, actual_amount: form.actual_amount, note: form.note.trim() || undefined }
  const validation = validateExecutionForm(payload)
  if (validation) { formError.value = validation; return }
  saving.value = true
  try {
    await mutate('/dca/executions', { ...payload, actual_amount: Number(payload.actual_amount) }, 'POST')
    dialogOpen.value = false
    form.due_date = ''; form.actual_amount = ''; form.note = ''
  } catch (failure) {
    formError.value = failure instanceof Error ? failure.message : '登记失败。'
  } finally {
    saving.value = false
  }
}

watch(() => session.value?.cache_scope, () => { selectedPlanId.value = ''; dialogOpen.value = false; formError.value = ''; form.due_date = ''; form.actual_amount = ''; form.note = '' })
</script>
