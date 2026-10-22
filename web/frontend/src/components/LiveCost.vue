<template>
  <PageHeader title="持有成本" subtitle="逐只基金的持仓成本、盈亏与赎回提示。" />
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!snapshot" @retry="refresh()" />
  <CostPanel :snapshot="snapshot ?? null" />
</template>
<script setup lang="ts">
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import CostPanel from './CostPanel.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadCost } from '../services/batch3Api'
const { data: snapshot, error, refreshing, refresh } = useAccountResource('cost', loadCost)
</script>
