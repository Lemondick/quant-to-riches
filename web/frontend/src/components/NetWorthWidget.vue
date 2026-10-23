<template>
  <div class="panel widget-panel">
    <div class="panel-head">
      <div><h2>{{ positionsOnly ? '持仓资产' : '净资产' }}</h2><p>资产配置速览<span data-update-time> · {{ snapshot.as_of ?? '暂无记录' }}</span></p></div>
      <RouterLink to="/net-worth" class="text-action">详情</RouterLink>
    </div>
    <div class="card-pad">
      <div class="widget-total"><b>{{ formatAmount(positionsOnly ? snapshot.total_assets : snapshot.net_assets) }}</b><span class="subtle">{{ positionsOnly ? '账本原币记录；负债未登记，净资产未计算' : '总资产 ' + money(snapshot.total_assets) }}</span></div>
      <div class="widget-bars">
        <div v-for="slice in slices" :key="slice.name" class="widget-bar-row">
          <span class="subtle">{{ slice.name }}</span>
          <div class="progress slim"><i :style="{ width: slice.weight + '%', background: slice.color }"></i></div>
          <b>{{ slice.pct }}</b>
        </div>
      </div>
      <p v-if="positionsOnly" class="subtle">原币金额合计与占比未折算汇率，不能用于跨币种资产比较。</p>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { allocationDonutItems, type NetWorthSnapshot } from '../services/batch5Api'
import { money } from '../utils/format'
const props = defineProps<{ snapshot: NetWorthSnapshot }>()
const positionsOnly = computed(() => props.snapshot.liabilities_recorded === false)
const formatAmount = (value: number) => positionsOnly.value ? value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : money(value)
const slices = computed(() => allocationDonutItems(props.snapshot, formatAmount))
</script>
<style scoped>
.widget-panel{margin-top:12px}
.widget-total{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}
.widget-total b{font-size:22px}
.widget-bars{display:flex;flex-direction:column;gap:8px}
.widget-bar-row{display:grid;grid-template-columns:52px 1fr 52px;align-items:center;gap:10px;font-size:12px}
.progress.slim{height:6px}
</style>
