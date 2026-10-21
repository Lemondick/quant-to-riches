<template>
  <PortfolioBacktestPanel :run-backtest="runBacktest" />
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import PortfolioBacktestPanel from './PortfolioBacktestPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { BacktestRequest, BacktestResult } from '../services/batch2Api'

/** 后端接口（流 A 并行开发）：POST api/v1/backtest/portfolio（worker op portfolio.backtest）；404 回退演示回测。 */
const { mutate } = useAccountResource('portfolio-backtest',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const notice = ref('')

async function runBacktest(request: BacktestRequest): Promise<BacktestResult> {
  notice.value = ''
  try {
    return await mutate<BacktestResult>('/backtest/portfolio', request, 'POST')
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockBacktestResult } = await import('../data/batch2')
      notice.value = '后端回测接口尚未就绪（A 并行开发中），当前为演示回测。'
      return mockBacktestResult(request)
    }
    throw failure
  }
}
</script>
