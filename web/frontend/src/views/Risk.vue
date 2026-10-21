<template>
  <LiveRisk v-if="liveBackend" />
  <div v-else>
    <PageHeader title="风险看板" subtitle="组合风险指标、净值回撤与压力测试。" />
    <RiskPanel :metrics="null" :stress="null" demo />
  </div>
</template>
<script setup lang="ts">
import PageHeader from '../components/PageHeader.vue'
import LiveRisk from '../components/LiveRisk.vue'
import RiskPanel from '../components/RiskPanel.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
</script>
