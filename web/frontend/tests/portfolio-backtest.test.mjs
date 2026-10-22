import test from 'node:test'
import assert from 'node:assert/strict'
import { backtestCurveObservations, validateBacktestRequest } from '../src/services/batch2Api.ts'
import { mockBacktestFunds, mockBacktestResult } from '../src/data/batch2.ts'

function validRequest() {
  return {
    funds: mockBacktestFunds(),
    start: '2025-10-21',
    end: '2026-10-21',
    rebalance: { mode: 'period', period: '3M' },
  }
}

test('backtest request validation covers weights, dates and rules', () => {
  assert.equal(validateBacktestRequest(validRequest()), null)
  assert.ok(validateBacktestRequest({ ...validRequest(), funds: [] }))
  const dup = validRequest()
  dup.funds = [...dup.funds, { ...dup.funds[0] }]
  assert.ok(validateBacktestRequest(dup))
  const badSum = validRequest()
  badSum.funds[0] = { ...badSum.funds[0], weight: 10 }
  assert.ok(validateBacktestRequest(badSum))
  const badDate = validRequest()
  badDate.start = '2026-10-21'
  badDate.end = '2025-10-21'
  assert.ok(validateBacktestRequest(badDate))
  const badThreshold = { ...validRequest(), rebalance: { mode: 'threshold', threshold_pct: 0 } }
  assert.ok(validateBacktestRequest(badThreshold))
})

test('mock backtest result is deterministic and well-formed', () => {
  const request = validRequest()
  const first = mockBacktestResult(request)
  const second = mockBacktestResult(request)
  assert.deepEqual(first, second)
  assert.ok(first.curve.length > 10)
  assert.ok(first.rebalance_events.length > 0)
  assert.ok(Number.isFinite(first.metrics.total_return_pct))
  assert.ok(first.metrics.max_drawdown_pct <= 0)
})

test('curve observations convert NAV to relative percent change', () => {
  const result = mockBacktestResult(validRequest())
  const observations = backtestCurveObservations(result)
  assert.equal(observations.labels.length, result.curve.length)
  assert.equal(observations.series[0].values[0], 0)
  const last = observations.series[0].values.at(-1)
  assert.ok(Math.abs(last - result.metrics.total_return_pct) < 0.01)
})
