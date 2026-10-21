<template>
  <RiskPanel v-if="metrics || stress" :metrics="metrics" :stress="stress" />
  <template v-else>
    <PageHeader title="风险看板" subtitle="组合风险指标、净值回撤与压力测试。" />
    <BackendState :error="error" :refreshing="refreshing && !loadedOnce" :has-data="loadedOnce" @retry="load()" />
    <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
    <div v-if="notReady" class="panel card-pad empty-state" role="status">
      <FileSearch :size="30" aria-hidden="true" />
      <h2>风险数据暂未就绪</h2>
      <p>后端风险接口（api/v1/risk/*，流 A 并行开发中）尚未可用，暂无数据可展示，不展示虚构数字。</p>
      <button class="btn" :disabled="refreshing" @click="load()">重试读取</button>
    </div>
  </template>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { FileSearch } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import RiskPanel from './RiskPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { RiskMetrics, StressTestResult } from '../services/batch3Api'

/** 后端接口（流 A 并行开发）：GET api/v1/risk/metrics · GET api/v1/risk/stress；
 *  未就绪时展示空态+说明（铁律：不虚构数字，不使用本地演示数据）。 */
const { error, refreshing, session, read } = useAccountResource('risk',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const metrics = ref<RiskMetrics | null>(null)
const stress = ref<StressTestResult | null>(null)
const loadedOnce = ref(false)
const loadError = ref('')
const notReady = ref(false)

async function load() {
  loadError.value = ''
  notReady.value = false
  let failureMessage = ''
  try {
    metrics.value = await read<RiskMetrics>('/risk/metrics')
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) notReady.value = true
    else failureMessage = failure instanceof Error ? failure.message : '风险指标读取失败。'
  }
  try {
    stress.value = await read<StressTestResult>('/risk/stress')
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) notReady.value = true
    else if (!failureMessage) failureMessage = failure instanceof Error ? failure.message : '压力测试读取失败。'
  }
  loadError.value = failureMessage
  loadedOnce.value = true
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
