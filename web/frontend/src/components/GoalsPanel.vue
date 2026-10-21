<template>
  <div class="goal-grid">
    <article v-for="item in cards" :key="item.goal.id" class="panel goal-card">
      <div class="panel-head">
        <div>
          <h2>{{ item.goal.name }}</h2>
          <p>目标日期 {{ item.goal.target_date }} · 假设年化 {{ (item.goal.assumed_annual_return * 100).toFixed(1) }}%</p>
        </div>
        <span class="tag" :class="item.deviation === 'ontrack' ? 'blue' : 'red'">{{ GOAL_DEVIATION_NAMES[item.deviation] }}</span>
      </div>
      <div class="card-pad">
        <div class="goal-amounts">
          <div><span class="subtle">目标金额</span><b>{{ money(item.goal.target_amount) }}</b></div>
          <div><span class="subtle">当前金额</span><b>{{ money(item.goal.current_amount) }}</b></div>
          <div><span class="subtle">缺口</span><b :class="item.projection.gap > 0 ? 'down' : 'up'">{{ item.projection.gap > 0 ? '缺 ' + money(item.projection.gap) : '已达标' }}</b></div>
        </div>
        <div class="progress" role="progressbar" :aria-valuenow="Math.round(item.progress)" aria-valuemin="0" aria-valuemax="100" :aria-label="item.goal.name + ' 进度'">
          <i :style="{ width: item.progress + '%' }"></i>
        </div>
        <div class="goal-foot">
          <span class="subtle">进度 {{ item.progress.toFixed(1) }}%</span>
          <span v-if="item.projection" class="subtle">
            按当前假设测算：每月需投 <b>{{ money(item.projection.required_monthly) }}</b>
          </span>
        </div>
        <div class="inline-actions">
          <button class="btn" @click="$emit('edit', item.goal)">编辑</button>
          <button class="btn" @click="$emit('remove', item.goal)">删除</button>
        </div>
      </div>
    </article>
  </div>
  <p class="disclaimer" role="note"><Info :size="14" aria-hidden="true" /><span><b>{{ GOAL_DISCLAIMER }}</b>：目标测算基于假设年化与等额月投的复利推算，仅用于规划参考，不构成收益承诺或投资建议。</span></p>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { Info } from 'lucide-vue-next'
import { GOAL_DEVIATION_NAMES, GOAL_DISCLAIMER, goalDeviation, goalProgress, type GoalProjection, type GoalTarget } from '../services/batch5Api'
import { money } from '../utils/format'

const props = defineProps<{
  goals: GoalTarget[]
  /** 每个目标的测算结果（id → projection；后端未就绪时用本地兜底测算）。 */
  projections: Record<string, GoalProjection>
}>()
defineEmits<{ edit: [goal: GoalTarget]; remove: [goal: GoalTarget] }>()

const cards = computed(() => props.goals.map(goal => ({
  goal,
  progress: goalProgress(goal),
  projection: props.projections[goal.id] ?? { required_monthly: 0, projected_amount: goal.current_amount, gap: goal.target_amount - goal.current_amount, on_track: goal.current_amount >= goal.target_amount, assumptions: { assumed_annual_return: goal.assumed_annual_return, frequency: 'monthly' as const }, disclaimer: '假设测算非承诺' },
  deviation: goalDeviation(goal, props.projections[goal.id] ?? { required_monthly: 0, projected_amount: goal.current_amount, gap: goal.target_amount - goal.current_amount, on_track: goal.current_amount >= goal.target_amount, assumptions: { assumed_annual_return: goal.assumed_annual_return, frequency: 'monthly' as const }, disclaimer: '假设测算非承诺' }),
})))
</script>
<style scoped>
.goal-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.goal-card{padding:0}
.goal-amounts{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}
.goal-amounts>div{display:flex;flex-direction:column;gap:4px}
.goal-foot{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin:10px 0 14px}
.disclaimer{display:flex;gap:8px;align-items:flex-start;margin-top:16px;padding:11px 14px;border:1px dashed #355b83;border-radius:8px;background:rgba(77,141,255,.06);color:#bad6fa;font-size:13px;line-height:1.7}
</style>
