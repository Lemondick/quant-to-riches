<template>
  <RouterLink to="/valuation" class="panel valuation-widget" aria-label="前往估值仪表盘">
    <div class="widget-head"><b>市场温度</b><span class="subtle">{{ snapshot.as_of }}</span></div>
    <div class="widget-body">
      <TemperatureGauge v-if="snapshot.temperature.score !== null" :score="snapshot.temperature.score" :label="snapshot.temperature.label" :size="150" />
      <span v-else class="subtle">{{ snapshot.temperature.label || '数据不足，暂无市场温度。' }}</span>
      <div class="widget-cheapest">
        <span class="subtle">最低估指数</span>
        <b v-if="cheapest">{{ cheapest.name }}</b><b v-else>—</b>
        <span v-if="cheapest" class="pct-line">PE {{ cheapest.pe_pct }}% · PB {{ cheapest.pb_pct }}%</span>
      </div>
    </div>
    <p v-for="warning in snapshot.warnings ?? []" :key="warning" class="subtle">{{ warning }}</p>
  </RouterLink>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import TemperatureGauge from './TemperatureGauge.vue'
import { cheapestIndex, type ValuationSnapshot } from '../services/batch2Api'
const props = defineProps<{ snapshot: ValuationSnapshot }>()
const cheapest = computed(() => cheapestIndex(props.snapshot))
</script>
<style scoped>
.valuation-widget{display:block;text-decoration:none;color:inherit;padding:14px 16px;margin-top:12px;transition:transform .25s}
.valuation-widget:hover{transform:translateY(-2px)}
.widget-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.subtle{font-size:11px;color:var(--text-2)}
.widget-body{display:flex;align-items:center;gap:18px}
.widget-cheapest{display:flex;flex-direction:column;gap:4px}
.widget-cheapest b{font-size:16px}
.pct-line{font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums}
</style>
