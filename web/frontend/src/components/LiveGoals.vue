<template>
  <PageHeader title="目标规划" subtitle="多目标进度、缺口测算与偏离告警。">
    <button class="btn primary" @click="openCreate()"><Plus :size="15" aria-hidden="true" />新建目标</button>
  </PageHeader>
  <BackendState :error="error" :refreshing="refreshing && !loadedOnce" :has-data="loadedOnce" @retry="load()" />
  <p v-if="mockNotice" class="feedback" role="status">{{ mockNotice }}</p>
  <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
  <GoalsPanel v-if="goals.length" :goals="goals" :projections="projections" @edit="openEdit" @remove="removeGoal" />
  <p v-else-if="loadedOnce" class="feedback" role="status">暂无目标，点击右上角「新建目标」创建第一个目标。</p>

  <AppDialog v-model="dialogOpen" :title="editing ? '编辑目标' : '新建目标'" description="目标金额、日期与假设年化用于测算每月需投金额。">
    <form class="stack-form" @submit.prevent="saveGoal">
      <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
      <p v-if="formNotice" class="feedback" role="status">{{ formNotice }}</p>
      <div class="form-grid">
        <label>目标名称<input v-model.trim="form.name" class="field" maxlength="60" required /></label>
        <label>目标日期<input v-model="form.target_date" class="field" type="date" required /></label>
      </div>
      <div class="form-grid">
        <label>目标金额（元）<input v-model.trim="form.target_amount" class="field" inputmode="decimal" placeholder="500000" required /></label>
        <label>当前金额（元）<input v-model.trim="form.current_amount" class="field" inputmode="decimal" placeholder="0" /></label>
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
import { reactive, ref, watch } from 'vue'
import { Plus } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import AppDialog from './AppDialog.vue'
import GoalsPanel from './GoalsPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import { projectGoalLocal, validateGoalForm, type GoalProjection, type GoalTarget } from '../services/batch5Api'

/** 后端接口（A 流并行开发）：GET/POST api/v1/goals · GET/PUT/DELETE api/v1/goals/{id} · POST api/v1/goals/{id}/project；
 *  未就绪时用契约 mock 保证可构建、可预览（页面明确标注待联调）。 */
const { error, refreshing, session, read, mutate } = useAccountResource('goals',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const goals = ref<GoalTarget[]>([])
const projections = ref<Record<string, GoalProjection>>({})
const loadedOnce = ref(false)
const loadError = ref('')
const mockNotice = ref('')
const saving = ref(false)

async function projectFor(goal: GoalTarget): Promise<GoalProjection> {
  try {
    return await read<GoalProjection>(`/goals/${goal.id}/project`)
  } catch {
    // 后端测算接口未就绪 → 本地兜底测算（同样醒目标注"假设测算非承诺"）。
    return projectGoalLocal({
      target_amount: goal.target_amount, target_date: goal.target_date, current_amount: goal.current_amount,
      assumed_annual_return: goal.assumed_annual_return,
    })
  }
}

async function load() {
  loadError.value = ''
  try {
    goals.value = await read<GoalTarget[]>('/goals')
    mockNotice.value = ''
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockGoals } = await import('../data/batch5')
      goals.value = mockGoals()
      mockNotice.value = '后端目标接口尚未就绪（A 并行开发中），当前为契约演示数据，待联调后切换为真实数据。'
    } else {
      loadError.value = failure instanceof Error ? failure.message : '目标列表读取失败。'
    }
  }
  const next: Record<string, GoalProjection> = {}
  for (const goal of goals.value) next[goal.id] = await projectFor(goal)
  projections.value = next
  loadedOnce.value = true
}

/* ---- 新建/编辑 ---- */
const dialogOpen = ref(false)
const editing = ref<GoalTarget | null>(null)
const formError = ref('')
const formNotice = ref('')
const form = reactive({ name: '', target_date: '', target_amount: '', current_amount: '', assumed_annual_return: '0.06', monthly_amount: '' })

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', target_date: '', target_amount: '', current_amount: '', assumed_annual_return: '0.06', monthly_amount: '' })
  formError.value = ''; formNotice.value = ''
  dialogOpen.value = true
}
function openEdit(goal: GoalTarget) {
  editing.value = goal
  Object.assign(form, {
    name: goal.name, target_date: goal.target_date, target_amount: String(goal.target_amount),
    current_amount: String(goal.current_amount), assumed_annual_return: String(goal.assumed_annual_return), monthly_amount: '',
  })
  formError.value = ''; formNotice.value = ''
  dialogOpen.value = true
}

async function saveGoal() {
  formError.value = ''
  const validation = validateGoalForm(form)
  if (validation) { formError.value = validation; return }
  saving.value = true
  const body = {
    name: form.name.trim(),
    target_amount: Number(form.target_amount),
    target_date: form.target_date,
    current_amount: form.current_amount === '' ? 0 : Number(form.current_amount),
    assumed_annual_return: form.assumed_annual_return === '' ? 0.06 : Number(form.assumed_annual_return),
  }
  const monthlyInput = form.monthly_amount === '' ? undefined : Number(form.monthly_amount)
  try {
    if (editing.value) await mutate(`/goals/${editing.value.id}`, body, 'PUT')
    else await mutate('/goals', body, 'POST')
    dialogOpen.value = false
    await load()
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404 && mockNotice.value) {
      // 演示模式：本地应用修改（不写后端），页面保持可预览。
      const projection = projectGoalLocal({ ...body, monthly_amount: monthlyInput })
      if (editing.value) {
        const index = goals.value.findIndex(item => item.id === editing.value!.id)
        if (index >= 0) goals.value[index] = { ...goals.value[index], ...body }
        projections.value = { ...projections.value, [editing.value.id]: projection }
      } else {
        const created: GoalTarget = { id: `local-${Date.now()}`, ...body }
        goals.value = [...goals.value, created]
        projections.value = { ...projections.value, [created.id]: projection }
      }
      dialogOpen.value = false
      formNotice.value = ''
      loadError.value = '演示模式：修改仅在本地预览生效，联调后将由后端持久化。'
    } else {
      formError.value = failure instanceof Error ? failure.message : '保存失败。'
    }
  } finally {
    saving.value = false
  }
}

async function removeGoal(goal: GoalTarget) {
  if (!window.confirm(`确定删除目标「${goal.name}」吗？`)) return
  try {
    await mutate(`/goals/${goal.id}`, {}, 'DELETE')
    await load()
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404 && mockNotice.value) {
      goals.value = goals.value.filter(item => item.id !== goal.id)
      const next = { ...projections.value }
      delete next[goal.id]
      projections.value = next
    } else {
      loadError.value = failure instanceof Error ? failure.message : '删除失败。'
    }
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
