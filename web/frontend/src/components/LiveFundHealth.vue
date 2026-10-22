<template>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!snapshot" @retry="refresh()" />
  <FundHealthPanel v-if="snapshot" :snapshot="snapshot" />
  <template v-else>
    <PageHeader title="基金体检" subtitle="按基金分组展示规模、经理、风格、费率四类发现。" />
    <p v-if="snapshot === null" class="feedback" role="status">暂无体检报告，等待后台生成。</p>
  </template>
</template>
<script setup lang="ts">
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import FundHealthPanel from './FundHealthPanel.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadFundHealth } from '../services/batch2Api'
const { data: snapshot, error, refreshing, refresh } = useAccountResource('fund-health', loadFundHealth)
</script>
