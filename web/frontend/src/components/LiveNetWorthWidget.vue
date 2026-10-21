<template>
  <NetWorthWidget v-if="snapshot" :snapshot="snapshot" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import NetWorthWidget from './NetWorthWidget.vue'
import { useAccountResource } from '../composables/useAccountResource'
import type { NetWorthSnapshot } from '../services/batch5Api'

/** 首页紧凑小组件的数据源：与净资产页同接口，独立缓存键；后端未就绪时不展示（不虚构数字）。 */
const { session, read } = useAccountResource('net-worth-widget',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const snapshot = ref<NetWorthSnapshot | null>(null)

async function load() {
  try {
    snapshot.value = await read<NetWorthSnapshot>('/net-worth?history_days=90')
  } catch {
    // 后端未就绪时小组件不展示（不虚构数字）。
    snapshot.value = null
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
