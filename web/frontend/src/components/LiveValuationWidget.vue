<template>
  <ValuationWidget v-if="snapshot" :snapshot="snapshot" />
  <p v-if="error" class="feedback error" role="status">估值数据暂不可用：{{ error instanceof Error ? error.message : '请稍后重试。' }}</p>
</template>
<script setup lang="ts">
import ValuationWidget from './ValuationWidget.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadValuation } from '../services/batch2Api'
const { data: snapshot, error } = useAccountResource('valuation-widget', loadValuation)
</script>
