<template>
  <PageHeader title="目标规划" subtitle="多目标进度、缺口测算与偏离告警。">
    <button class="btn primary" @click="openCreate()"><Plus :size="15" aria-hidden="true" />新建目标</button>
  </PageHeader>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p>
  <p v-for="failure in data?.projectionErrors ?? []" :key="failure" class="feedback error" role="alert">{{ failure }}</p>
  <GoalsPanel v-if="goals.length" :goals="goals" :projections="projections" @edit="openEdit" @remove="removeGoal" />
  <p v-else-if="data" class="feedback" role="status">暂无目标，点击右上角「新建目标」创建第一个目标。</p>

  <AppDialog v-model="dialogOpen" :title="editing ? '编辑目标' : '新建目标'" description="目标金额、日期与假设年化用于测算每月需投金额。">
    <form class="stack-form" @submit.prevent="saveGoal">
      <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
      <div class="form-grid">
        <label>目标名称<input v-model.trim="form.name" class="field" maxlength="60" required /></label>
        <label>目标日期<input v-model="form.target_date" class="field" type="date" required /></label>
      </div>
      <div class="form-grid">
        <label>目标金额（元）<input v-model.trim="form.target_amount" class="field" inputmode="decimal" placeholder="500000" required /></label>
        <label v-if="!editing">当前金额（元，可选）<input v-model.trim="form.current_amount" class="field" inputmode="decimal" placeholder="留空使用账本净资产" /></label>
      </div>
      <div class="form-grid">
        <label>假设年化（如 0.06）<input v-model.trim="form.assumed_annual_return" class="field" inputmode="decimal" placeholder="0.06" /></label>
        <label>每月定投金额（元，可选）<input v-model.trim="form.monthly_amount" class="field" inputmode="decimal" placeholder="不填则倒推需投" /></label>
      </div>
      <button class="btn primary" type="submit" :disabled="saving">{{ saving ? '保存中…' : (editing ? '保存修改' : '创建目标') }}</button>
    </form>
  </AppDialog>
</template>
<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Plus } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import AppDialog from './AppDialog.vue'
import GoalsPanel from './GoalsPanel.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadGoals, validateGoalForm, type GoalTarget } from '../services/batch5Api'

const monthlyAmounts = ref<Record<string, number>>({})
const { data, error, refreshing, refresh, session, mutate } = useAccountResource('goals',
  (api, signal) => loadGoals(api, signal, monthlyAmounts.value))
const goals = computed(() => data.value?.goals ?? [])
const projections = computed(() => data.value?.projections ?? {})
const actionError = ref('')
const saving = ref(false)

/* ---- 新建/编辑 ---- */
const dialogOpen = ref(false)
const editing = ref<GoalTarget | null>(null)
const formError = ref('')
const form = reactive({ name: '', target_date: '', target_amount: '', current_amount: '', assumed_annual_return: '0.06', monthly_amount: '' })

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', target_date: '', target_amount: '', current_amount: '', assumed_annual_return: '0.06', monthly_amount: '' })
  formError.value = ''
  dialogOpen.value = true
}
function openEdit(goal: GoalTarget) {
  editing.value = goal
  Object.assign(form, {
    name: goal.name, target_date: goal.target_date, target_amount: String(goal.target_amount),
    current_amount: String(goal.current_amount), assumed_annual_return: String(goal.assumed_annual_return), monthly_amount: monthlyAmounts.value[goal.id] === undefined ? '' : String(monthlyAmounts.value[goal.id]),
  })
  formError.value = ''
  dialogOpen.value = true
}

async function saveGoal() {
  formError.value = ''
  const validation = validateGoalForm(form)
  if (validation) { formError.value = validation; return }
  const monthlyInput = form.monthly_amount === '' ? undefined : Number(form.monthly_amount)
  if (monthlyInput !== undefined && (!Number.isFinite(monthlyInput) || monthlyInput < 0)) { formError.value = '每月定投金额不能为负数。'; return }
  saving.value = true
  const body = {
    name: form.name.trim(),
    target_amount: Number(form.target_amount),
    target_date: form.target_date,
    assumed_annual_return: form.assumed_annual_return === '' ? 0.06 : Number(form.assumed_annual_return),
  }
  try {
    const saved = editing.value
      ? await mutate<GoalTarget>(`/goals/${encodeURIComponent(editing.value.id)}`, body, 'PUT')
      : await mutate<GoalTarget>('/goals', { ...body, ...(form.current_amount === '' ? {} : { current_amount: Number(form.current_amount) }) }, 'POST')
    const previousMonthly = monthlyAmounts.value[saved.id]
    if (monthlyInput === undefined) delete monthlyAmounts.value[saved.id]
    else monthlyAmounts.value[saved.id] = monthlyInput
    if (previousMonthly !== monthlyInput) await refresh()
    dialogOpen.value = false
  } catch (failure) {
    formError.value = failure instanceof Error ? failure.message : '保存失败。'
  } finally {
    saving.value = false
  }
}

async function removeGoal(goal: GoalTarget) {
  if (!window.confirm(`确定删除目标「${goal.name}」吗？`)) return
  actionError.value = ''
  try {
    await mutate(`/goals/${encodeURIComponent(goal.id)}`, {}, 'DELETE')
    delete monthlyAmounts.value[goal.id]
  } catch (failure) {
    actionError.value = failure instanceof Error ? failure.message : '删除失败。'
  }
}

watch(() => session.value?.cache_scope, () => { dialogOpen.value = false; editing.value = null; monthlyAmounts.value = {}; actionError.value = ''; formError.value = '' })
</script>
