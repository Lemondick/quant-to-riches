import { computed } from 'vue'
import { useAccountResource } from './useAccountResource'
import { useComputeTask } from './useComputeTask'
import { normalizeValuation } from '../services/batch2Api'
import type { BackendSession } from '../services/dataApi'

export function useValuationTask(name: string) {
  const account = useAccountResource(name, (api, signal) => api.read<BackendSession>('/auth/session', signal))
  const verifiedSession = computed(() => account.data.value ? account.session.value : undefined)
  const computation = useComputeTask('valuation.percentiles', verifiedSession, normalizeValuation, { path: '/valuation?background=true', method: 'GET' })
  return { ...computation, snapshot: computation.result, verifiedSession, sessionError: account.error, sessionRefreshing: account.refreshing, retrySession: account.refresh }
}
