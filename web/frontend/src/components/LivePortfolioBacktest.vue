<template>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!session" @retry="refresh()" />
  <PortfolioBacktestPanel :run-backtest="runBacktest" />
</template>
<script setup lang="ts">
import PortfolioBacktestPanel from './PortfolioBacktestPanel.vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { normalizePortfolioBacktest, type BacktestRequest, type BacktestResult } from '../services/batch2Api'

/** 后端接口：POST api/v1/backtest/portfolio（worker op portfolio.backtest）。 */
const { error, refreshing, refresh, session, mutate } = useAccountResource('portfolio-backtest',
  (api, signal) => api.read<unknown>('/auth/session', signal))

async function runBacktest(request: BacktestRequest): Promise<BacktestResult> {
  const result = await mutate<Parameters<typeof normalizePortfolioBacktest>[0]>('/backtest/portfolio', request, 'POST')
  return normalizePortfolioBacktest(result)
}
</script>
