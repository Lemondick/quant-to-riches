<template>
  <div class="gauge" role="img" :aria-label="`市场温度计 ${score} 分，${label}`">
    <svg :viewBox="`0 0 ${W} ${H}`" :width="size" :height="size * 0.62" aria-hidden="true">
      <path :d="track" fill="none" :stroke="trackColor" :stroke-width="stroke" stroke-linecap="round" />
      <path :d="valueArc" fill="none" :stroke="valueColor" :stroke-width="stroke" stroke-linecap="round" />
      <text :x="W / 2" :y="H - 14" text-anchor="middle" class="gauge-score" :fill="valueColor">{{ score }}</text>
      <text :x="W / 2" :y="H - 32" text-anchor="middle" class="gauge-label">{{ label }}</text>
    </svg>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { temperatureLabel } from '../services/batch2Api'

const props = withDefaults(defineProps<{ score: number; label?: string; size?: number }>(), { size: 220 })

const W = 200, H = 124, stroke = 14
const cx = W / 2, cy = H - 8, r = (W - stroke - 8) / 2
const score = computed(() => Math.max(0, Math.min(100, Math.round(props.score))))
const label = computed(() => props.label || temperatureLabel(score.value))
const track = computed(() => `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`)
function arc(frac: number): string {
  const end = Math.PI * (1 - Math.max(0, Math.min(1, frac)))
  return `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r * Math.cos(end)} ${cy - r * Math.sin(end)}`
}
const valueArc = computed(() => arc(score.value / 100))
const valueColor = computed(() => score.value < 30 ? '#3ddc84' : score.value < 55 ? '#4d8dff' : score.value < 70 ? '#e8a33d' : '#e05d5d')
const trackColor = 'rgba(34,51,77,.8)'
</script>
<style scoped>
.gauge{display:inline-block}
.gauge-score{font-size:26px;font-weight:800}
.gauge-label{font-size:12px;fill:var(--text-2)}
</style>
