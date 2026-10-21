<template>
  <PageHeader title="持有成本" subtitle="逐只基金的持仓成本、盈亏与赎回提示。" />
  <BackendState :error="error" :refreshing="refreshing && !snapshot" :has-data="!!snapshot" @retry="load()" />
  <CostPanel :snapshot="snapshot" />
  <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import CostPanel from './CostPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { CostSnapshot } from '../services/batch3Api'

/** 后端接口（流 A 并行开发）：GET api/v1/cost/positions；
 *  未就绪时展示空态+说明（铁律：不虚构数字，不使用本地演示数据）。 */
const { error, refreshing, session, read } = useAccountResource('cost',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const snapshot = ref<CostSnapshot | null>(null)
const loadError = ref('')

async function load() {
  loadError.value = ''
  try {
    snapshot.value = await read<CostSnapshot>('/cost/positions')
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      snapshot.value = null
    } else {
      loadError.value = failure instanceof Error ? failure.message : '持有成本读取失败。'
    }
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
