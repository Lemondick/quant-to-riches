<template>
  <PageHeader title="净资产总览" subtitle="总资产、净资产、资产配置与历史曲线。" />
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!snapshot" @retry="refresh()" />
  <NetWorthPanel v-if="snapshot" :snapshot="snapshot" />
</template>
<script setup lang="ts">
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import NetWorthPanel from './NetWorthPanel.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadNetWorth } from '../services/batch5Api'
const { data: snapshot, error, refreshing, refresh } = useAccountResource('net-worth', loadNetWorth)
</script>
