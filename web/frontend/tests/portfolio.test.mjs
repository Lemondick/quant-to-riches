import test from 'node:test'
import assert from 'node:assert/strict'
import { portfolioTotals, recordTrade } from '../src/utils/portfolio.ts'

const account = {
  cash: 1000,
  holdings: [{ code: '510300', name: '示例基金', type: '基金', quantity: 10, price: 12, cost: 10, previousClose: 11 }],
  transactions: [],
}
const input = {
  date: '2026-10-16T12:00:00+08:00',
  code: '510300',
  name: '示例基金',
  type: '基金',
  direction: '买入',
  quantity: 10,
  price: 11,
  fee: 1,
  note: '',
}

test('buy updates cash and weighted holding cost without mutating the account', () => {
  const result = recordTrade(account, input, 'new-buy')
  assert.equal(result.cash, 889)
  assert.equal(result.holdings[0].quantity, 20)
  assert.equal(result.holdings[0].cost, 10.55)
  assert.equal(result.holdings[0].price, 12)
  assert.equal(result.transactions[0].amount, 110)
  assert.equal(account.cash, 1000)
  assert.equal(account.holdings[0].quantity, 10)
})

test('sell calculates realized profit and retains the remaining cost basis', () => {
  const result = recordTrade(account, { ...input, direction: '卖出', quantity: 4, price: 13 }, 'sell')
  assert.equal(result.cash, 1051)
  assert.equal(result.holdings[0].quantity, 6)
  assert.equal(result.holdings[0].cost, 10)
  assert.equal(result.transactions[0].realized, 11)
  const closed = recordTrade(account, { ...input, direction: '卖出', quantity: 10, price: 13 }, 'close')
  assert.equal(closed.holdings.length, 0)
})

test('cash dividend adds its net amount without changing position quantity', () => {
  const result = recordTrade(account, { ...input, direction: '分红', price: 25, quantity: 0, fee: 0.5 }, 'dividend')
  assert.equal(result.cash, 1024.5)
  assert.equal(result.transactions[0].realized, 24.5)
  assert.deepEqual(result.holdings, account.holdings)
})

test('overselling, insufficient cash, malformed dates and invalid numeric input are rejected', () => {
  for (const change of [
    { quantity: 1000 },
    { direction: '卖出', quantity: 11 },
    { price: 0 },
    { fee: -1 },
    { price: NaN },
    { date: 'invalid' },
    { code: 'no-code' },
  ]) {
    assert.throws(() => recordTrade(account, { ...input, ...change }, 'invalid'))
  }
  assert.equal(account.transactions.length, 0)
  assert.equal(account.cash, 1000)
})

test('portfolio totals reconcile market value, cost, cash and daily change', () => {
  assert.deepEqual(portfolioTotals(account), {
    invested: 120,
    cost: 100,
    daily: 10,
    profit: 20,
    total: 1120,
    returnRate: 20,
  })
  const empty = portfolioTotals({ cash: 0, holdings: [], transactions: [] })
  assert.equal(empty.returnRate, 0)
  assert.equal(empty.total, 0)
})
