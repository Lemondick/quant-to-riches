<template>
  <ValuationWidget v-if="snapshot" :snapshot="snapshot" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import ValuationWidget from './ValuationWidget.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { ValuationSnapshot } from '../services/batch2Api'

/** 首页紧凑小组件的数据源：与估值页同接口，独立缓存键。 */
const { session, read } = useAccountResource('valuation-widget',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const snapshot = ref<ValuationSnapshot | null>(null)

async function load() {
  try {
    snapshot.value = await read<ValuationSnapshot>('/valuation')
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockValuation } = await import('../data/batch2')
      snapshot.value = mockValuation()
    }
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
