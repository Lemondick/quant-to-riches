<template>
  <div class="donut-wrap">
    <div class="donut" :style="{ background: 'conic-gradient(' + segments + ')' }" role="img" :aria-label="items.map(item => item.name + ' ' + item.pct).join('，')">
      <div class="donut-hole"><strong>{{ center }}</strong><span>{{ label }}</span></div>
    </div>
    <div class="donut-list"><div v-for="item in items" :key="item.name"><span><i :style="{ background: item.color }" aria-hidden="true"></i>{{ item.name }}</span><b>{{ item.value }}</b><em>{{ item.pct }}</em></div></div>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ items: { name: string; value: string; pct: string; color: string; weight: number }[]; center: string; label: string }>()
const segments = computed(() => {
  const total = props.items.reduce((sum, item) => sum + item.weight, 0)
  if (!total) return '#243750 0% 100%'
  let sum = 0
  return props.items.map(item => { const start = sum; sum += item.weight / total * 100; return item.color + ' ' + start + '% ' + sum + '%' }).join(',')
})
</script>
