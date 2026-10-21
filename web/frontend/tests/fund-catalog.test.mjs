import test from 'node:test'
import assert from 'node:assert/strict'
import { catalogPath, catalogPercent, catalogStamp, validateCatalog } from '../src/services/fundCatalog.ts'

test('catalog request identities include combined filters, page and trimmed search', () => {
  const a = catalogPath(' 110020 ', 2, { strategy_tags: ['指数策略', '绝对收益'], share_class: ['__unknown'] })
  const b = catalogPath('110020', 2, { share_class: ['__unknown'], strategy_tags: ['绝对收益', '指数策略'] })
  assert.equal(a, b)
  assert.notEqual(a, catalogPath('110020', 1, { share_class: ['__unknown'] }))
  const url = new URL(a, 'https://explicit-fixture.test')
  assert.deepEqual(url.searchParams.getAll('strategy_tags'), ['指数策略', '绝对收益'].sort())
  assert.equal(url.searchParams.get('q'), '110020')
})
test('missing values are distinct from actual zero, and wrong page/identity responses are rejected', () => {
  assert.equal(catalogPercent(null), '—')
  assert.equal(catalogPercent('0'), '0.00%')
  assert.equal(catalogPercent('NaN'), '—')
  assert.equal(catalogStamp('2026-10-20T08:00:00Z'), '2026-10-20 16:00:00 北京时间')
  assert.equal(catalogStamp(null), '来源未提供')
  const data = {
    page: 1,
    page_size: 50,
    total: 1,
    quality_status: 'unknown_time',
    items: [{ symbol: '110020', name: '明确测试夹具', strategy_tags: [], investment_scope: [] }],
  }
  assert.equal(validateCatalog(data, catalogPath('', 1, {})), data)
  assert.throws(() => validateCatalog(data, catalogPath('', 2, {})))
  assert.throws(() => validateCatalog({ ...data, items: [{ ...data.items[0], symbol: '12' }] }, catalogPath('', 1, {})))
  assert.throws(() => validateCatalog({ ...data, quality_status: 'invalid' }, catalogPath('', 1, {})))
})
