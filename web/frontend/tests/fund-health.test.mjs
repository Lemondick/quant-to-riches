import test from 'node:test'
import assert from 'node:assert/strict'
import { isInsufficient, orderFindings, worstSeverity } from '../src/services/batch2Api.ts'
import { mockFundHealth } from '../src/data/batch2.ts'

test('insufficient data is explicitly flagged', () => {
  const snapshot = mockFundHealth()
  const third = snapshot.reports[2]
  assert.ok(third.findings.filter(isInsufficient).length === 2)
  assert.ok(!isInsufficient(snapshot.reports[0].findings[0]))
})

test('findings follow the scale/manager/style/fee order', () => {
  const snapshot = mockFundHealth()
  const shuffled = { ...snapshot.reports[0], findings: [...snapshot.reports[0].findings].reverse() }
  assert.deepEqual(
    orderFindings(shuffled).map((f) => f.kind),
    ['scale_change', 'manager_change', 'style_drift', 'fee_level'],
  )
})

test('worst severity bubbles up to the fund header', () => {
  const snapshot = mockFundHealth()
  assert.equal(worstSeverity(snapshot.reports[0]), 'warn')
  assert.equal(worstSeverity(snapshot.reports[1]), 'critical')
  assert.equal(worstSeverity(snapshot.reports[2]), 'info')
})
