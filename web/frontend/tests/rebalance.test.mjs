import test from 'node:test'
import assert from 'node:assert/strict'
import { buildRebalanceAdvice, validateRebalanceTarget } from '../src/services/batch2Api.ts'
import { mockRebalanceState } from '../src/data/batch2.ts'

test('rebalance target must sum to 100 and stay in range', () => {
  assert.equal(
    validateRebalanceTarget({
      equity_pct: 60,
      bond_pct: 25,
      cash_pct: 15,
      max_industry_pct: 20,
      deviation_threshold_pct: 3,
    }),
    null,
  )
  assert.ok(
    validateRebalanceTarget({
      equity_pct: 60,
      bond_pct: 25,
      cash_pct: 10,
      max_industry_pct: 20,
      deviation_threshold_pct: 3,
    }),
  )
  assert.ok(
    validateRebalanceTarget({
      equity_pct: -5,
      bond_pct: 25,
      cash_pct: 80,
      max_industry_pct: 20,
      deviation_threshold_pct: 3,
    }),
  )
})

test('deviation advice flags only classes beyond threshold', () => {
  const { advices, hasAlert } = buildRebalanceAdvice(mockRebalanceState())
  const byClass = Object.fromEntries(advices.map((a) => [a.class, a]))
  assert.equal(byClass.equity.alert, true) // 64.8 vs 60，偏离 4.8 >= 3
  assert.equal(byClass.bond.alert, false) // 22.1 vs 25，偏离 2.9 < 3
  assert.equal(byClass.cash.alert, false)
  assert.ok(byClass.equity.suggestion?.startsWith('建议卖出股票类'))
  assert.equal(byClass.bond.suggestion, null)
  assert.equal(hasAlert, true)
})

test('industry concentration alert fires above the cap', () => {
  const state = mockRebalanceState()
  state.target.max_industry_pct = 15
  const { industryAlerts } = buildRebalanceAdvice(state)
  assert.deepEqual(
    industryAlerts.map((a) => a.name),
    ['白酒'],
  )
})
