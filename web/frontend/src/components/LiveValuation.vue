<template>
  <ValuationPanel v-if="snapshot" :snapshot="snapshot" />
  <template v-else>
    <PageHeader title="估值仪表盘" subtitle="六大指数 PE/PB 历史百分位、ERP 与市场温度计。" />
    <BackendState :error="error" :refreshing="refreshing && !loadedOnce" :has-data="loadedOnce" @retry="load()" />
    <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
  </template>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import ValuationPanel from './ValuationPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { ValuationSnapshot } from '../services/batch2Api'

/** 后端接口（流 A 并行开发）：GET api/v1/valuation（worker op valuation.percentiles）；404 回退演示数据。 */
const { error, refreshing, session, read } = useAccountResource('valuation',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const snapshot = ref<ValuationSnapshot | null>(null)
const loadedOnce = ref(false)
const loadError = ref('')

async function load() {
  loadError.value = ''
  try {
    snapshot.value = await read<ValuationSnapshot>('/valuation')
    loadedOnce.value = true
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockValuation } = await import('../data/batch2')
      snapshot.value = mockValuation()
      loadedOnce.value = true
      loadError.value = '后端估值接口尚未就绪（A 并行开发中），当前为演示数据。'
    } else {
      loadError.value = failure instanceof Error ? failure.message : '估值数据读取失败。'
    }
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
