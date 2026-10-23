<template>
  <BackendState :error="sessionError" :refreshing="sessionRefreshing" :has-data="!!snapshot" @retry="retrySession()" />
  <ComputeTaskStatus v-if="verifiedSession" :task="task" :error="error" :notice="notice" :requesting="requesting" :cancelling="cancelling" :recoverable="recoverable" @cancel="cancel()" @refresh="refresh()" />
  <template v-if="snapshot">
    <ValuationPanel :snapshot="snapshot" />
    <LiveSentimentWidget />
  </template>
  <PageHeader v-else title="估值仪表盘" subtitle="指数 PE/PB 历史百分位、ERP 与市场温度计。" />
  <button v-if="verifiedSession" class="btn section-space" :disabled="busy" @click="reloadValuation()">刷新估值</button>
</template>
<script setup lang="ts">
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import ValuationPanel from './ValuationPanel.vue'
import LiveSentimentWidget from './LiveSentimentWidget.vue'
import ComputeTaskStatus from './ComputeTaskStatus.vue'
import { useValuationTask } from '../composables/useValuationTask'
const { snapshot, verifiedSession, sessionError, sessionRefreshing, retrySession, task, error, notice, requesting, cancelling, recoverable, busy, reloadValuation, refresh, cancel } = useValuationTask('valuation-session')
</script>
