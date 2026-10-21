<template>
  <LivePortfolioBacktest v-if="liveBackend" />
  <PortfolioBacktestPanel v-else demo :run-backtest="mockRun" />
</template>
<script setup lang="ts">
import LivePortfolioBacktest from '../components/LivePortfolioBacktest.vue'
import PortfolioBacktestPanel from '../components/PortfolioBacktestPanel.vue'
import { mockBacktestResult } from '../data/batch2'
import type { BacktestRequest, BacktestResult } from '../services/batch2Api'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
async function mockRun(request: BacktestRequest): Promise<BacktestResult> {
  await new Promise(resolve => setTimeout(resolve, 350))
  return mockBacktestResult(request)
}
</script>
