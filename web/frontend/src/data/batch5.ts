/** Batch5 新页面的本地 mock 数据：后端（A 流）联调前保证可构建、可预览。情绪指标不做 mock（无源即空态）。 */
import type { GoalTarget, NetWorthSnapshot, DcaTracking } from '../services/batch5Api'

export function mockGoals(): GoalTarget[] {
  return [
    { id: 'mock-goal-1', name: '子女教育金', target_amount: 500000, target_date: '2031-09-01', current_amount: 86000, assumed_annual_return: 0.06 },
    { id: 'mock-goal-2', name: '五年后换车', target_amount: 300000, target_date: '2030-12-01', current_amount: 120000, assumed_annual_return: 0.05 },
    { id: 'mock-goal-3', name: '应急储备金', target_amount: 100000, target_date: '2027-06-01', current_amount: 95000, assumed_annual_return: 0.02 },
  ]
}

export function mockNetWorth(): NetWorthSnapshot {
  const history: { date: string; net_assets: number }[] = []
  let value = 1180000
  for (let i = 89; i >= 0; i--) {
    const date = new Date(2026, 9, 21 - i)
    value += Math.round((Math.sin(i / 6) * 9000 + (i % 7) * 1400 - 5000) / 100) * 100
    history.push({ date: date.toISOString().slice(0, 10), net_assets: value })
  }
  return {
    total_assets: 1380000,
    net_assets: value,
    as_of: '2026-10-21',
    allocation: { equity: 690000, bond: 345000, cash: 276000, other: 69000 },
    history,
  }
}

export function mockDcaTracking(): DcaTracking {
  return {
    plan_id: 'mock-dca-plan-1',
    plan_name: '沪深300 定投（月投 3000）',
    missed_alert_days: 3,
    periods: [
      { due_date: '2026-05-05', status: 'done', planned_amount: 3000, actual_amount: 3000, executed_at: '2026-05-05T09:30:00', note: null },
      { due_date: '2026-06-05', status: 'done', planned_amount: 3000, actual_amount: 3100, executed_at: '2026-06-05T09:30:00', note: '补投 100' },
      { due_date: '2026-07-05', status: 'missed', planned_amount: 3000, actual_amount: null, executed_at: null, note: '卡内余额不足' },
      { due_date: '2026-08-05', status: 'done', planned_amount: 3000, actual_amount: 2900, executed_at: '2026-08-05T09:32:00', note: null },
      { due_date: '2026-09-05', status: 'skipped', planned_amount: 3000, actual_amount: null, executed_at: null, note: '主动暂停一期' },
      { due_date: '2026-10-05', status: 'done', planned_amount: 3000, actual_amount: 3000, executed_at: '2026-10-05T09:30:00', note: null },
      { due_date: '2026-11-05', status: 'pending', planned_amount: 3000, actual_amount: null, executed_at: null, note: null },
    ],
  }
}
