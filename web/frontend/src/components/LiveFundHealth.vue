<template>
  <FundHealthPanel v-if="snapshot" :snapshot="snapshot" />
  <template v-else>
    <PageHeader title="基金体检" subtitle="按基金分组展示规模、经理、风格、费率四类发现。" />
    <BackendState :error="error" :refreshing="refreshing && !loadedOnce" :has-data="loadedOnce" @retry="load()" />
    <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
  </template>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import FundHealthPanel from './FundHealthPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { FundHealthSnapshot } from '../services/batch2Api'

/** 后端接口（流 A 并行开发）：GET api/v1/funds/health（worker op fund.health_check）；404 回退演示数据。 */
const { error, refreshing, session, read } = useAccountResource('fund-health',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const snapshot = ref<FundHealthSnapshot | null>(null)
const loadedOnce = ref(false)
const loadError = ref('')

async function load() {
  loadError.value = ''
  try {
    snapshot.value = await read<FundHealthSnapshot>('/funds/health')
    loadedOnce.value = true
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockFundHealth } = await import('../data/batch2')
      snapshot.value = mockFundHealth()
      loadedOnce.value = true
      loadError.value = '后端体检接口尚未就绪（A 并行开发中），当前为演示数据。'
    } else {
      loadError.value = failure instanceof Error ? failure.message : '体检报告读取失败。'
    }
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
