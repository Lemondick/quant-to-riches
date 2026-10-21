import test from 'node:test'
import assert from 'node:assert/strict'
import {
  sectorIdentity,
  sectorValue,
  sectorTone,
  sectorQuality,
  sectorCatalogStatus,
} from '../src/utils/sectorPresentation.ts'

const row = {
  name: '同名板块',
  code: 'BK0001',
  source: 'eastmoney',
  category: 'industry',
  change_rate: '0',
  quality_status: 'valid',
  source_time: '2026-10-20T15:00:00+08:00',
}
test('sector identity keeps supplier and category independent, actual zero is neutral', () => {
  assert.notEqual(sectorIdentity(row), sectorIdentity({ ...row, source: 'sina' }))
  assert.notEqual(sectorIdentity(row), sectorIdentity({ ...row, category: 'concept' }))
  assert.equal(sectorValue(row), 0)
  assert.equal(sectorTone(row), '')
  for (const value of [null, '', 'NaN', 'Infinity', false, []])
    assert.equal(sectorValue({ ...row, change_rate: value }), null)
  for (const quality_status of ['invalid', 'unavailable']) assert.equal(sectorValue({ ...row, quality_status }), null)
})
test('quality distinguishes invalid, missing, stale and unknown clocks without receipt time', () => {
  assert.match(sectorQuality({ ...row, quality_status: 'invalid' }), /校验失败/)
  assert.match(sectorQuality({ ...row, change_rate: null }), /暂无报价/)
  assert.match(
    sectorQuality({
      ...row,
      source_time: null,
      fetched_at: '2026-10-21T08:00:00+08:00',
      quality_status: 'unknown_time',
    }),
    /来源时间未知/,
  )
  assert.match(sectorQuality({ ...row, quality_status: 'stale', is_stale: true }), /已过期/)
})
test('directory coverage, pagination, missing quotes and time uncertainty coexist', () => {
  const catalog = {
    received: 497,
    total: null,
    unquoted: 1,
    quote_total: 496,
    quote_payload_complete: true,
    source_time_known: false,
    catalog_stale: true,
    stale: true,
    observed_at: '2026-10-20T15:00:00+08:00',
  }
  const text = sectorCatalogStatus(catalog, 'closed', Date.parse('2026-10-21T08:00:00+08:00'))
  for (const expected of [/497/, /总数未知/, /旧目录/, /1 项暂无报价/, /496/, /部分或全部报价时间未知/])
    assert.match(text, expected)
  assert.doesNotMatch(text, /全市场完整/)
  assert.match(sectorCatalogStatus({ received: 0, http_ok: true, parse_ok: false }, 'closed'), /解析失败/)
  assert.match(sectorCatalogStatus({ received: 10 }, 'closed'), /报价缺失数量未知/)
})
