<template>
  <PageHeader title="净资产总览" subtitle="总资产、净资产、资产配置与历史曲线。" />
  <BackendState :error="error" :refreshing="refreshing && !loadedOnce" :has-data="loadedOnce" @retry="load()" />
  <p v-if="mockNotice" class="feedback" role="status">{{ mockNotice }}</p>
  <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
  <NetWorthPanel v-if="snapshot" :snapshot="snapshot" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import NetWorthPanel from './NetWorthPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { NetWorthSnapshot } from '../services/batch5Api'

/** 后端接口（A 流并行开发）：GET api/v1/net-worth（?history_days=）；
 *  未就绪时用契约 mock 保证可构建、可预览（页面明确标注待联调）。 */
const { error, refreshing, session, read } = useAccountResource('net-worth',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const snapshot = ref<NetWorthSnapshot | null>(null)
const loadedOnce = ref(false)
const loadError = ref('')
const mockNotice = ref('')

async function load() {
  loadError.value = ''
  try {
    snapshot.value = await read<NetWorthSnapshot>('/net-worth?history_days=90')
    mockNotice.value = ''
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockNetWorth } = await import('../data/batch5')
      snapshot.value = mockNetWorth()
      mockNotice.value = '后端净资产接口尚未就绪（A 并行开发中），当前为契约演示数据，待联调后切换为真实数据。'
    } else {
      loadError.value = failure instanceof Error ? failure.message : '净资产数据读取失败。'
    }
  }
  loadedOnce.value = true
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
