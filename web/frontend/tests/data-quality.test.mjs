import test from 'node:test'
import assert from 'node:assert/strict'
import { updateTime, oldestUpdate, usableEstimate } from '../src/utils/dataQuality.ts'

test('unknown and malformed source times never fall back to the browser clock', () => {
  for (const value of [null, undefined, '', 'invalid', '2026-02-30']) assert.equal(updateTime(value), '更新时间：未知')
  assert.equal(updateTime('2026-10-16'), '更新时间：2026-10-16')
  assert.equal(updateTime('2026-10-16T15:00+08:00'), '更新时间：2026-10-16 15:00')
  assert.equal(updateTime('2026-10-16T07:00:01Z'), '更新时间：2026-10-16 15:00:01')
  assert.equal(oldestUpdate([{ updated_at: '2026-10-16' }, { updated_at: null }]), null)
})

test('official NAV, null and stale estimates never enter intraday profit', () => {
  for (const value of [
    null,
    { valuation_type: 'latest_nav', estimate_nav: '1', change_rate: '1' },
    { valuation_type: 'intraday_estimate', estimate_nav: null, change_rate: '1' },
    { valuation_type: 'intraday_estimate', estimate_nav: '1', change_rate: null },
    { valuation_type: 'intraday_estimate', estimate_nav: '1', change_rate: '1', quality_status: 'stale' },
  ])
    assert.equal(usableEstimate(value), false)
  assert.equal(
    usableEstimate({
      valuation_type: 'intraday_estimate',
      estimate_nav: '1',
      change_rate: '0',
      quality_status: 'valid',
    }),
    true,
  )
})
