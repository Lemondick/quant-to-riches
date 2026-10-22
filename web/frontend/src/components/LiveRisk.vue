<template>
  <PageHeader title="风险看板" subtitle="组合风险指标、净值回撤与压力测试。" />
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <p v-for="failure in data?.failures ?? []" :key="failure" class="feedback error" role="alert">{{ failure }}</p>
  <RiskPanel :metrics="data?.metrics ?? null" :stress="data?.stress ?? null" />
</template>
<script setup lang="ts">
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import RiskPanel from './RiskPanel.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadRiskMetrics, loadStressTest } from '../services/batch3Api'
const { data, error, refreshing, refresh } = useAccountResource('risk', async (api, signal) => {
  const [metrics, stress] = await Promise.allSettled([loadRiskMetrics(api, signal), loadStressTest(api, signal)])
  if (metrics.status === 'rejected' && stress.status === 'rejected') throw metrics.reason
  return { metrics: metrics.status === 'fulfilled' ? metrics.value : null, stress: stress.status === 'fulfilled' ? stress.value : null,
    failures: [metrics, stress].filter(item => item.status === 'rejected').map(item => item.reason instanceof Error ? item.reason.message : '风险数据暂不可用。') }
})
</script>
