import { useAccountResource } from './useAccountResource'
import { validateDashboard, type DashboardSummary } from '../services/dataApi'

export function useDashboardSummary() {
  return useAccountResource('dashboard', async (api, signal, userId) => validateDashboard(await api.read<DashboardSummary>('/dashboard/summary?separateNav=true', signal), userId))
}
