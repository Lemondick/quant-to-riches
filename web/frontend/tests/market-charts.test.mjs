import test from 'node:test'
import assert from 'node:assert/strict'
import { aggregateCandles, navCandles } from '../src/utils/marketCandles.ts'
import { readPreparedPublic } from '../src/services/preparedPublic.ts'
import { ApiError, createDataApi } from '../src/services/dataApi.ts'

test('native error_code identifies preparation while the server message remains redacted', async () => {
  const api = createDataApi(
    '/api/v1',
    async () =>
      new Response(JSON.stringify({ error_code: 'public_snapshot_unavailable', message: 'private-debug-info' }), {
        status: 503,
      }),
  )
  await assert.rejects(
    api.read('/funds/023530/detail'),
    (error) => error.code === 'public_snapshot_unavailable' && !error.message.includes('private-debug-info'),
  )
})

test('weekly index candles retain actual OHLC and sum only supplied volume', () => {
  const rows = [
    { date: '2026-10-12', open: 10, high: 13, low: 9, close: 12, volume: 20 },
    { date: '2026-10-13', open: 12, high: 14, low: 11, close: 13, volume: 30 },
  ]
  const bars = aggregateCandles(rows, 'week')
  assert.equal(bars.length, 1)
  assert.deepEqual([bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume], [10, 14, 9, 13, 50])
})
test('fund daily NAV remains flat; weekly NAV aggregates observations without invented intraday prices', () => {
  const rows = navCandles([
    { nav_date: '2026-10-12', unit_nav: '1.2' },
    { nav_date: '2026-10-13', unit_nav: '1.3' },
  ])
  assert.deepEqual([rows[0].open, rows[0].high, rows[0].low, rows[0].close], [1.2, 1.2, 1.2, 1.2])
  assert.equal(rows[0].volume, undefined)
  const bar = aggregateCandles(rows, 'week')[0]
  assert.deepEqual([bar.open, bar.high, bar.low, bar.close], [1.2, 1.3, 1.2, 1.3])
})
test('invalid prices, inconsistent OHLC and duplicate observations cannot enter a chart', () => {
  const valid = { date: '2026-10-12', open: 10, high: 13, low: 9, close: 12 }
  assert.throws(() => aggregateCandles([{ ...valid, high: 8 }], 'day'))
  assert.throws(() => aggregateCandles([valid, valid], 'day'))
  assert.throws(() => aggregateCandles([{ ...valid, date: '2026-02-30' }], 'day'))
  assert.throws(() => navCandles([{ nav_date: '2026-10-12', unit_nav: 'NaN' }]))
})
test('preparing public observations are polled inside one load and return the first available data', async () => {
  let calls = 0
  const result = await readPreparedPublic(
    async () => {
      if (++calls < 3) throw new ApiError(503, 'preparing', 'public_snapshot_unavailable')
      return { nav_history: ['real-observation'] }
    },
    new AbortController().signal,
    { pause: async () => {}, attempts: 4 },
  )
  assert.equal(calls, 3)
  assert.deepEqual(result.nav_history, ['real-observation'])
})
test('real failures and authentication errors are never disguised as public preparation', async () => {
  for (const failure of [new ApiError(401, 'login'), new ApiError(503, 'database', 'database_unavailable')]) {
    let calls = 0
    await assert.rejects(
      readPreparedPublic(async () => {
        calls++
        throw failure
      }, new AbortController().signal),
      (error) => error === failure,
    )
    assert.equal(calls, 1)
  }
})
test('changing selection aborts preparation without another provider request', async () => {
  const controller = new AbortController()
  let calls = 0
  await assert.rejects(
    readPreparedPublic(async () => {
      calls++
      controller.abort()
      throw new ApiError(503, 'preparing', 'public_snapshot_unavailable')
    }, controller.signal),
    (error) => error.name === 'AbortError',
  )
  assert.equal(calls, 1)
})
