<template>
  <RiskWidget v-if="metrics" :snapshot="metrics" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import RiskWidget from './RiskWidget.vue'
import { useAccountResource } from '../composables/useAccountResource'
import type { RiskMetrics } from '../services/batch3Api'

/** 首页紧凑小组件的数据源：与风险页同接口，独立缓存键；后端未就绪时不展示（不虚构数字）。 */
const { session, read } = useAccountResource('risk-widget',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const metrics = ref<RiskMetrics | null>(null)

async function load() {
  try {
    metrics.value = await read<RiskMetrics>('/risk/metrics')
  } catch {
    metrics.value = null
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
