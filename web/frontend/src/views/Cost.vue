<template>
  <LiveCost v-if="liveBackend" />
  <div v-else>
    <PageHeader title="持有成本" subtitle="逐只基金的持仓成本、盈亏与赎回提示。" />
    <CostPanel :snapshot="null" demo />
  </div>
</template>
<script setup lang="ts">
import PageHeader from '../components/PageHeader.vue'
import LiveCost from '../components/LiveCost.vue'
import CostPanel from '../components/CostPanel.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
</script>
