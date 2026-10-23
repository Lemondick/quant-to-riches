<template>
  <PageHeader title="净资产总览" subtitle="账本持仓资产、配置与历史曲线；负债尚未登记时不计算净资产。" />
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
