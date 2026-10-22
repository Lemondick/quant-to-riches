<template>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!state" @retry="refresh()" />
  <p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p>
  <RebalancePanel v-if="state" :state="state" :saving="mutating" :notice="notice" @save="save" />
  <PageHeader v-else title="再平衡" subtitle="设定目标配置，监控实际偏离并获取调仓建议。" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import RebalancePanel from './RebalancePanel.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadRebalance, rebalanceTargetBody, type RebalanceTarget } from '../services/batch2Api'
const { data: state, error, refreshing, refresh, session, mutate, mutating } = useAccountResource('rebalance', loadRebalance)
const notice = ref(''), actionError = ref('')
watch(() => session.value?.cache_scope, () => { notice.value = ''; actionError.value = '' })
async function save(target: RebalanceTarget) {
  notice.value = ''; actionError.value = ''
  try { await mutate('/rebalance/targets', rebalanceTargetBody(target), 'PUT'); notice.value = '目标配置已保存。' }
  catch (failure) { actionError.value = failure instanceof Error ? failure.message : '保存失败。' }
}
</script>
