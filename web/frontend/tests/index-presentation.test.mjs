import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { indexCards, indexHistoryResponse } from '../src/utils/indexPresentation.ts'

// Authority is read only by this explicit synthetic Node fixture, never browser production.
const registry = JSON.parse(
  readFileSync(new URL('../../backend/src/quant_assistant/providers/index_registry.json', import.meta.url)),
).definitions
function snapshot() {
  return {
    index_registry: structuredClone(registry),
    indices: registry.map((d, i) => ({
      id: d.id,
      symbol: d.exchange_code,
      name: d.name,
      market: d.market,
      currency: d.currency,
      asset_type: 'index',
      price: '100',
      change: String((i % 3) - 1),
      change_rate: String((i % 3) - 1),
      source_time: '2026-10-20T13:35:00Z',
      fetched_at: '2026-10-20T13:35:01Z',
      quality_status: 'valid',
      source: 'explicit-local-layout-fixture',
    })),
    index_statuses: registry.map((d) => ({ id: d.id, quality_status: 'valid', quality_reason: null })),
  }
}
test('fifteen cards use registry order and stable IDs despite shuffled quotes', () => {
  const data = snapshot()
  data.indices.reverse()
  data.index_registry.reverse()
  const cards = indexCards(data)
  assert.deepEqual(
    cards.map((c) => c.definition.id),
    registry.map((d) => d.id),
  )
  assert.equal(cards.length, 15)
  assert.ok(cards.every((card) => card.definition.product_status === 'confirmed'))
  assert.ok(cards.every((card) => card.definition.id !== 'cn.csi.1000'))
  assert.equal(cards[0].quote.id, registry[0].id)
})
test('one missing item preserves the other fourteen without inventing zero', () => {
  const data = snapshot()
  data.indices.splice(4, 1)
  const cards = indexCards(data)
  assert.equal(cards.length, 15)
  assert.equal(cards[4].value, '—')
  assert.equal(cards[4].quality, 'unavailable')
  assert.equal(cards.filter((c) => c.quote).length, 14)
})
test('conflicting identities and duplicate IDs never borrow a quote', () => {
  for (const [key, value] of [
    ['symbol', '600000'],
    ['market', 'US'],
    ['currency', 'USD'],
    ['id', 'us.sp500'],
    ['name', '平安银行'],
    ['asset_type', 'stock'],
  ]) {
    const data = snapshot()
    data.indices[0][key] = value
    const card = indexCards(data)[0]
    assert.equal(card.value, '—', key)
    assert.notEqual(card.quality, 'valid', key)
  }
  const data = snapshot()
  data.indices.push({ ...data.indices[0] })
  assert.equal(indexCards(data)[0].quality, 'invalid')
})
test('null empty malformed and nonfinite numbers are never coerced to zero', () => {
  for (const key of ['price', 'change', 'change_rate'])
    for (const value of [null, '', 'NaN', 'Infinity', 'not-a-number']) {
      const data = snapshot()
      data.indices[0][key] = value
      assert.equal(indexCards(data)[0].value, '—', `${key}:${value}`)
    }
})
test('each own sign controls color, including neutral zero and reordered data', () => {
  const cards = indexCards(snapshot())
  assert.deepEqual(
    cards.slice(0, 3).map((c) => c.tone),
    ['down', 'neutral', 'up'],
  )
  assert.equal(cards[1].change, '0.00')
  assert.equal(cards[1].percent, '0.00%')
  assert.equal(cards[2].change, '+1.00')
})
test('quality and fallback are explicit per card and invalid status hides values', () => {
  for (const quality of ['valid', 'stale', 'unknown_time', 'invalid', 'unavailable']) {
    const data = snapshot()
    data.index_statuses[0].quality_status = quality
    const card = indexCards(data)[0]
    assert.equal(card.quality, quality)
    assert.ok(card.qualityLabel)
    assert.equal(card.value === '—', ['invalid', 'unavailable'].includes(quality))
  }
  const data = snapshot()
  data.indices[0].cache_fallback = true
  assert.equal(indexCards(data)[0].quality, 'stale')
})
test('source clock uses each market zone with DST and never receipt or browser clock', () => {
  const data = snapshot()
  const us = indexCards(data).find((c) => c.definition.id === 'us.sp500')
  assert.match(us.sourceLabel, /2026-10-20 09:35:00/)
  assert.match(us.sourceLabel, /America\/New_York/)
  data.indices[0].source_time = null
  assert.equal(indexCards(data)[0].sourceLabel, '来源时间未知')
  assert.equal(indexCards(data)[0].quality, 'unknown_time')
  data.indices[0].source_time = '2026-10-20T10:00:00'
  assert.equal(indexCards(data)[0].sourceLabel, '来源时间未知')
})
test('missing registry never fabricates a fifteen item product list', () => {
  assert.deepEqual(indexCards({ indices: snapshot().indices }), [])
})
test('a valid collection status cannot hide an invalid or stale quote row', () => {
  const data = snapshot()
  data.indices[0].quality_status = 'invalid'
  assert.equal(indexCards(data)[0].value, '—')
  data.indices[0].quality_status = 'stale'
  assert.equal(indexCards(data)[0].quality, 'stale')
})
test('impossible source dates and timezone offsets stay unknown', () => {
  for (const clock of ['2026-02-30T10:00:00Z', '2026-10-20T10:00:00+14:01', '2026-10-20T24:00:00Z']) {
    const data = snapshot()
    data.indices[0].source_time = clock
    assert.equal(indexCards(data)[0].sourceLabel, '来源时间未知')
  }
})
test('history response must match exact requested ID and real series contract', () => {
  const data = { id: 'cn.csi.300', items: [], quality_status: 'unavailable', quality_reason: 'history_not_connected' }
  assert.equal(indexHistoryResponse(data, 'cn.csi.300'), data)
  assert.throws(() => indexHistoryResponse(data, 'cn.sse.composite'), /指数历史标识/)
  assert.throws(() => indexHistoryResponse({ ...data, items: [{}] }, 'cn.csi.300'), /指数历史/)
  assert.throws(() => indexHistoryResponse({ id: 'cn.csi.300' }, 'cn.csi.300'), /指数历史/)
})
test('explicitly invalid history never enters the successful chart cache', () => {
  const payload = {
    id: 'cn.sse.composite',
    quality_status: 'invalid',
    quality_reason: 'invalid_source_time',
    items: [{ date: '2099-01-05', open: '100', high: '110', low: '90', close: '105' }],
  }
  assert.throws(() => indexHistoryResponse(payload, 'cn.sse.composite'), /未通过质量校验/)
  assert.throws(
    () => indexHistoryResponse({ ...payload, quality_status: 'unrecognized' }, 'cn.sse.composite'),
    /未通过质量校验/,
  )
  for (const status of [undefined, 'valid', 'stale', 'unknown_time'])
    assert.equal(
      indexHistoryResponse(
        { ...payload, items: [{ ...payload.items[0], date: '2026-10-19' }], quality_status: status },
        'cn.sse.composite',
      ).quality_status,
      status,
    )
})
