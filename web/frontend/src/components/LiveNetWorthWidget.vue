<template>
  <NetWorthWidget v-if="snapshot" :snapshot="snapshot" />
  <p v-if="error" class="feedback error" role="status">净资产数据暂不可用：{{ error instanceof Error ? error.message : '请稍后重试。' }}</p>
</template>
<script setup lang="ts">
import NetWorthWidget from './NetWorthWidget.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadNetWorth } from '../services/batch5Api'
const { data: snapshot, error } = useAccountResource('net-worth-widget', loadNetWorth)
</script>
