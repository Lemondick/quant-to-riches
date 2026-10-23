<template>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!session" @retry="refresh()" />
  <template v-if="verifiedSession">
    <ComputeTaskStatus :task="task" :error="taskError" :notice="notice" :requesting="requesting" :cancelling="cancelling" :recoverable="recoverable" @cancel="cancel()" @refresh="refreshTask()" />
    <PortfolioBacktestPanel :key="session?.cache_scope" :run-backtest="runBacktest" :task-result="result" :task-running="busy" />
  </template>
</template>
<script setup lang="ts">
import PortfolioBacktestPanel from './PortfolioBacktestPanel.vue'
import ComputeTaskStatus from './ComputeTaskStatus.vue'
import { computed } from 'vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { normalizePortfolioBacktest, type BacktestRequest, type BacktestResult } from '../services/batch2Api'
import { useComputeTask } from '../composables/useComputeTask'
import type { BackendSession } from '../services/dataApi'

/** 后端接口：POST api/v1/backtest/portfolio（worker op portfolio.backtest）。 */
const { data: verifiedSession, error, refreshing, refresh, session } = useAccountResource('portfolio-backtest',
  (api, signal) => api.read<BackendSession>('/auth/session', signal))
const currentSession = computed(() => verifiedSession.value ? session.value : undefined)
const { task, result, error: taskError, notice, requesting, cancelling, recoverable, busy, start, cancel, refresh: refreshTask } = useComputeTask('portfolio.backtest', currentSession,
  value => normalizePortfolioBacktest(value as Parameters<typeof normalizePortfolioBacktest>[0]))

async function runBacktest(request: BacktestRequest): Promise<BacktestResult | null> {
  await start({ path: '/backtest/portfolio?background=true', body: request })
  return result.value
}
</script>
