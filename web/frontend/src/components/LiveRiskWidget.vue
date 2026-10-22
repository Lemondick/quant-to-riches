<template>
  <RiskWidget v-if="snapshot" :snapshot="snapshot" />
  <p v-if="error" class="feedback error" role="status">风险数据暂不可用：{{ error instanceof Error ? error.message : '请稍后重试。' }}</p>
</template>
<script setup lang="ts">
import RiskWidget from './RiskWidget.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadRiskMetrics } from '../services/batch3Api'
const { data: snapshot, error } = useAccountResource('risk-widget', loadRiskMetrics)
</script>
