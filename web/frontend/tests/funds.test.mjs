import test from 'node:test'
import assert from 'node:assert/strict'
import { fundList, fundPeriods, rankFunds, summarizeFunds, formatReturn } from '../src/data/funds.ts'

const filters = { period: 'dayChange', category: '全部', query: '', ascending: false }

test('all six periods rank by their own returns, including week and year', () => {
  const expectedLeaders = ['512000', '159995', '320007', '320007', '320007', '159995']
  for (const [index, period] of fundPeriods.entries()) {
    const ranked = rankFunds(fundList, { ...filters, period: period.key })
    assert.equal(ranked[0].code, expectedLeaders[index], period.label)
    assert.ok(
      ranked.every(
        (fund, position) => !position || parseFloat(ranked[position - 1][period.key]) >= parseFloat(fund[period.key]),
      ),
    )
  }
})

test('ascending order reverses performance order without changing source data', () => {
  const originalCodes = fundList.map((fund) => fund.code)
  const descending = rankFunds(fundList, { ...filters, period: 'y1' })
  const ascending = rankFunds(fundList, { ...filters, period: 'y1', ascending: true })
  assert.deepEqual(
    ascending.map((fund) => fund.code),
    descending.map((fund) => fund.code).reverse(),
  )
  assert.deepEqual(
    fundList.map((fund) => fund.code),
    originalCodes,
  )
  assert.equal(ascending[0].code, '003096')
})

test('type and trimmed name, code or manager queries compose', () => {
  assert.equal(rankFunds(fundList, { ...filters, query: ' 510300 ' })[0].code, '510300')
  assert.equal(rankFunds(fundList, { ...filters, category: '混合型', query: '张坤' })[0].code, '005827')
  assert.equal(rankFunds(fundList, { ...filters, category: '债券型', query: '张坤' }).length, 0)
  assert.equal(rankFunds(fundList, { ...filters, query: 'etf' }).length, 4)
})

test('summaries follow the filtered period rather than the table sort direction', () => {
  const ranked = rankFunds(fundList, { ...filters, category: '混合型', period: 'y1', ascending: true })
  const summary = summarizeFunds(ranked, 'y1')
  assert.equal(summary.count, 3)
  assert.equal(summary.top.code, '320007')
  assert.equal(summary.last.code, '003096')
  assert.equal(summary.positiveCount, 2)
  assert.ok(Math.abs(summary.average - (25.32 + 9.72 - 5.24) / 3) < 1e-10)
})

test('empty results display unavailable values instead of invented zero returns', () => {
  const summary = summarizeFunds([], 'w1')
  assert.equal(summary.top, null)
  assert.equal(summary.last, null)
  assert.equal(summary.average, null)
  assert.equal(summary.count, 0)
  assert.equal(formatReturn(summary.average), '—')
  assert.equal(formatReturn(0), '0.00%')
  assert.equal(formatReturn(1.25), '+1.25%')
  assert.equal(formatReturn(-0.42), '-0.42%')
})
