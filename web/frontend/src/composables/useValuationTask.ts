import { computed, onScopeDispose } from 'vue'
import { useAccountResource } from './useAccountResource'
import { useComputeTask, type ComputeRequest } from './useComputeTask'
import { normalizeValuation } from '../services/batch2Api'
import type { BackendSession } from '../services/dataApi'

export function useValuationTask(name: string) {
  const account = useAccountResource(name, (api, signal) => api.read<BackendSession>('/auth/session', signal))
  const verifiedSession = computed(() => account.data.value ? account.session.value : undefined)
  const request: ComputeRequest = { path: '/valuation?background=true', method: 'GET' }
  const computation = useComputeTask('valuation.percentiles', verifiedSession, normalizeValuation, request)
  const timer = setInterval(() => {
    if (document.visibilityState === 'visible' && verifiedSession.value && computation.result.value && !computation.busy.value && !computation.error.value)
      void computation.start(request, true)
  }, 60000)
  onScopeDispose(() => clearInterval(timer))
  return { ...computation, snapshot: computation.result, verifiedSession, reloadValuation: () => computation.start(request), sessionError: account.error, sessionRefreshing: account.refreshing, retrySession: account.refresh }
}
