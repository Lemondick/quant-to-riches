<template>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!snapshot" @retry="refresh()" />
  <template v-if="snapshot">
    <ValuationPanel :snapshot="snapshot" />
    <LiveSentimentWidget />
  </template>
  <PageHeader v-else title="估值仪表盘" subtitle="指数 PE/PB 历史百分位、ERP 与市场温度计。" />
</template>
<script setup lang="ts">
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import ValuationPanel from './ValuationPanel.vue'
import LiveSentimentWidget from './LiveSentimentWidget.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadValuation } from '../services/batch2Api'
const { data: snapshot, error, refreshing, refresh } = useAccountResource('valuation', loadValuation)
</script>
