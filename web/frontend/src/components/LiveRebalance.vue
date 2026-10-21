<template>
  <RebalancePanel v-if="state" :state="state" :saving="mutating" :notice="notice" @save="save" />
  <template v-else>
    <PageHeader title="再平衡" subtitle="设定目标配置，监控实际偏离并获取调仓建议。" />
    <BackendState :error="error" :refreshing="refreshing && !loadedOnce" :has-data="loadedOnce" @retry="load()" />
    <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
  </template>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import RebalancePanel from './RebalancePanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { RebalanceState, RebalanceTarget } from '../services/batch2Api'

/** 后端接口（流 A 并行开发）：GET/PUT api/v1/rebalance；404 时走本地演示数据。 */
const { error, refreshing, session, read, mutate, mutating } = useAccountResource('rebalance',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const state = ref<RebalanceState | null>(null)
const loadedOnce = ref(false)
const loadError = ref('')
const notice = ref('')

async function load() {
  loadError.value = ''
  try {
    // 契约 §3 路由；联调前若后端未就绪则回退演示数据，保证页面可构建可看。
    state.value = await read<RebalanceState>('/rebalance')
    loadedOnce.value = true
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockRebalanceState } = await import('../data/batch2')
      state.value = mockRebalanceState()
      loadedOnce.value = true
      loadError.value = '后端再平衡接口尚未就绪（A 并行开发中），当前为演示数据。'
    } else {
      loadError.value = failure instanceof Error ? failure.message : '再平衡数据读取失败。'
    }
  }
}

async function save(target: RebalanceTarget) {
  notice.value = ''
  try {
    state.value = await mutate<RebalanceState>('/rebalance', { target }, 'PUT')
    notice.value = '目标配置已保存。'
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      if (state.value) state.value = { ...state.value, target }
      notice.value = '后端保存接口尚未就绪，已在本地暂存本次修改（演示模式）。'
    } else {
      notice.value = ''
      loadError.value = failure instanceof Error ? failure.message : '保存失败。'
    }
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
