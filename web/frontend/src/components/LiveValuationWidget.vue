<template>
  <ValuationWidget v-if="snapshot" :snapshot="snapshot" />
  <ComputeTaskStatus v-if="verifiedSession && (!snapshot || error)" :task="task" :error="error" :notice="notice" :requesting="requesting" :cancelling="cancelling" :recoverable="recoverable" @cancel="cancel()" @refresh="refresh()" />
  <p v-if="sessionError" class="feedback error" role="status">估值数据暂不可用：{{ sessionError instanceof Error ? sessionError.message : '请稍后重试。' }}</p>
</template>
<script setup lang="ts">
import ValuationWidget from './ValuationWidget.vue'
import ComputeTaskStatus from './ComputeTaskStatus.vue'
import { useValuationTask } from '../composables/useValuationTask'
const { snapshot, verifiedSession, sessionError, task, error, notice, requesting, cancelling, recoverable, refresh, cancel } = useValuationTask('valuation-widget-session')
</script>
