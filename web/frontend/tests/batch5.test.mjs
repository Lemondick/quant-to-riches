import test from 'node:test'
import assert from 'node:assert/strict'
import {
  monthsUntil, projectGoalLocal, goalProgress, goalDeviation,
  validateGoalForm, allocationDonutItems, netWorthObservations,
  sentimentEmptyReason, dcaTrackingStats, dcaPeriodDeviation, validateExecutionForm,
  GOAL_DISCLAIMER,
} from '../src/services/batch5Api.ts'
import { mockGoals, mockNetWorth, mockDcaTracking } from '../src/data/batch5.ts'

test('monthsUntil counts whole months to target date', () => {
  assert.equal(monthsUntil('2027-10-21', new Date('2026-10-21')), 12)
  assert.equal(monthsUntil('2026-10-21', new Date('2026-10-21')), 0)
  assert.equal(monthsUntil('2025-01-01', new Date('2026-10-21')), 0)
  assert.equal(monthsUntil('not-a-date', new Date('2026-10-21')), 0)
})

test('projectGoalLocal projection carries the mandatory disclaimer', () => {
  const projection = projectGoalLocal({ target_amount: 500000, target_date: '2031-09-01', current_amount: 86000, assumed_annual_return: 0.06 })
  assert.equal(projection.disclaimer, GOAL_DISCLAIMER)
  assert.ok(projection.required_monthly > 0)
  assert.ok(projection.gap > 0)
  assert.equal(projection.on_track, false)
})

test('projectGoalLocal on_track when target already reachable', () => {
  const projection = projectGoalLocal({ target_amount: 100000, target_date: '2027-06-01', current_amount: 95000, assumed_annual_return: 0.02 })
  assert.equal(projection.on_track, projection.gap <= 0)
})

test('goalProgress clamps to 0-100', () => {
  assert.equal(goalProgress({ target_amount: 100, current_amount: 25 }), 25)
  assert.equal(goalProgress({ target_amount: 100, current_amount: 250 }), 100)
  assert.equal(goalProgress({ target_amount: 0, current_amount: 25 }), 0)
})

test('goalDeviation badges: overdue and large gap are critical', () => {
  const [education] = mockGoals()
  const overdue = { ...education, target_date: '2025-01-01' }
  const proj = projectGoalLocal({ target_amount: overdue.target_amount, target_date: overdue.target_date, current_amount: overdue.current_amount, assumed_annual_return: overdue.assumed_annual_return })
  assert.equal(goalDeviation(overdue, proj), 'critical')
  const reached = { ...education, current_amount: education.target_amount }
  const projOk = projectGoalLocal({ target_amount: reached.target_amount, target_date: reached.target_date, current_amount: reached.current_amount, assumed_annual_return: reached.assumed_annual_return })
  assert.equal(goalDeviation(reached, projOk), 'ontrack')
})

test('validateGoalForm rejects invalid input', () => {
  const valid = { name: '教育金', target_amount: '500000', target_date: '2031-09-01', current_amount: '86000', assumed_annual_return: '0.06' }
  assert.equal(validateGoalForm(valid), null)
  assert.ok(validateGoalForm({ ...valid, name: '' }))
  assert.ok(validateGoalForm({ ...valid, target_amount: '0' }))
  assert.ok(validateGoalForm({ ...valid, target_date: '2031/09/01' }))
  assert.ok(validateGoalForm({ ...valid, assumed_annual_return: '1.5' }))
  assert.ok(validateGoalForm({ ...valid, current_amount: '-1' }))
})

test('allocationDonutItems covers the four classes and sums to 100%', () => {
  const slices = allocationDonutItems(mockNetWorth(), value => String(value))
  assert.equal(slices.length, 4)
  assert.deepEqual(slices.map(s => s.name), ['股票类', '债券类', '现金类', '其他'])
  const total = slices.reduce((sum, s) => sum + s.weight, 0)
  assert.ok(Math.abs(total - 100) < 0.01, `total=${total}`)
})

test('netWorthObservations produces one series for ChartCard', () => {
  const obs = netWorthObservations(mockNetWorth())
  assert.equal(obs.series.length, 1)
  assert.equal(obs.series[0].name, '净资产')
  assert.ok(obs.labels.length > 0)
  assert.equal(obs.series[0].values.length, obs.labels.length)
})

test('sentiment empty state never fabricates data', () => {
  assert.equal(sentimentEmptyReason(null), '情绪数据源未接入，暂无情绪指标。')
  assert.equal(sentimentEmptyReason({ items: [], warning: '数据源未接入' }), '数据源未接入')
  assert.equal(sentimentEmptyReason({ items: [{ name: '融资余额', value: '1.8', unit: '万亿', updated_at: 'x', source: 'y' }] }), null)
})

test('dcaTrackingStats counts per-status periods and amount deviation', () => {
  const stats = dcaTrackingStats(mockDcaTracking())
  assert.equal(stats.total_periods, 7)
  assert.equal(stats.done_periods, 4)
  assert.equal(stats.missed_periods, 1)
  assert.equal(stats.pending_periods, 1)
  assert.equal(stats.skipped_periods, 1)
  // 已扣款期：计划 4×3000=12000；实际 3000+3100+2900+3000=12000
  assert.equal(stats.planned_total, 12000)
  assert.equal(stats.actual_total, 12000)
  assert.equal(stats.amount_deviation, 0)
  assert.equal(stats.execution_rate_pct, 80)
})

test('dcaPeriodDeviation only applies to done periods with actual amount', () => {
  const [done] = mockDcaTracking().periods
  assert.equal(dcaPeriodDeviation({ ...done, actual_amount: 3100, planned_amount: 3000 }), 100)
  assert.equal(dcaPeriodDeviation({ ...done, status: 'pending', actual_amount: null }), null)
})

test('validateExecutionForm rejects bad registration input', () => {
  const valid = { plan_id: 'p1', due_date: '2026-11-05', actual_amount: '3000' }
  assert.equal(validateExecutionForm(valid), null)
  assert.ok(validateExecutionForm({ ...valid, plan_id: '' }))
  assert.ok(validateExecutionForm({ ...valid, due_date: '2026/11/05' }))
  assert.ok(validateExecutionForm({ ...valid, actual_amount: '0' }))
})
