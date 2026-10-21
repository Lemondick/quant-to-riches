import test from 'node:test'
import assert from 'node:assert/strict'
import {
  computeDrawdown, fmtRiskPct, fmtRiskRatio, riskMethodNote,
  maxStressLoss, stressBarWidth,
  redemptionTierText, redemptionHintText, COST_TAX_NOTE_FALLBACK,
  researchItemId, researchSymbols, filterResearchItems, sortResearchDesc,
  validateEmailSettings, emailStatusName, defaultEmailSettings,
} from '../src/services/batch3Api.ts'

test('drawdown tracks the running peak', () => {
  const points = computeDrawdown([
    { date: '2026-01-01', nav: 1 }, { date: '2026-01-02', nav: 1.1 },
    { date: '2026-01-03', nav: 0.99 }, { date: '2026-01-04', nav: 1.2 },
  ])
  assert.equal(points[0].drawdown_pct, 0)
  assert.equal(points[1].drawdown_pct, 0)
  assert.ok(Math.abs(points[2].drawdown_pct - (-10)) < 1e-9)
  assert.equal(points[3].drawdown_pct, 0)
})

test('drawdown tolerates invalid nav points', () => {
  const points = computeDrawdown([{ date: 'x', nav: NaN }, { date: 'y', nav: -1 }])
  assert.deepEqual(points.map(p => p.drawdown_pct), [0, 0])
})

test('risk formatting', () => {
  assert.equal(fmtRiskPct(0.0234), '2.34%')
  assert.equal(fmtRiskPct(-0.18), '-18.00%')
  assert.equal(fmtRiskPct(null), '—')
  assert.equal(fmtRiskPct(undefined), '—')
  assert.equal(fmtRiskPct(NaN), '—')
  assert.equal(fmtRiskRatio(1.25), '1.25')
  assert.equal(fmtRiskRatio(null), '—')
})

test('method note echoes the backend-declared method', () => {
  assert.ok(riskMethodNote('历史模拟法95%').includes('历史模拟法95%'))
  assert.ok(riskMethodNote('').includes('暂未声明'))
})

test('stress bar widths normalize to the max loss', () => {
  const scenarios = [
    { name: 'a', index_drop_pct: 30, est_loss_pct: 20, est_loss_amount: null },
    { name: 'b', index_drop_pct: 15, est_loss_pct: 10, est_loss_amount: 100 },
  ]
  assert.equal(maxStressLoss(scenarios), 20)
  assert.equal(stressBarWidth(scenarios[0], 20), 100)
  assert.equal(stressBarWidth(scenarios[1], 20), 50)
  assert.equal(stressBarWidth(scenarios[0], 0), 0)
})

test('redemption tier text', () => {
  assert.equal(redemptionTierText({ min_days: 7, max_days: 30, rate: 0.015 }), '持有7–30天：1.50%')
  assert.equal(redemptionTierText({ min_days: 365, max_days: null, rate: 0 }), '持有≥365天：0.00%')
})

test('redemption hint falls back to the generic tax note', () => {
  const hint = redemptionHintText({ symbol: '000001', cost_total: '1', price: '1', unrealized_pnl: '0', unrealized_pnl_rate: '0', annualized_return: null, breakeven_price: null, holding_days: 10 })
  assert.deepEqual(hint.tiers, [])
  assert.equal(hint.taxNote, COST_TAX_NOTE_FALLBACK)
  const withTiers = redemptionHintText({
    symbol: '000001', cost_total: '1', price: '1', unrealized_pnl: '0', unrealized_pnl_rate: '0',
    annualized_return: null, breakeven_price: null, holding_days: 10,
    redemption: { fee_tiers: [{ min_days: 0, max_days: 7, rate: 0.015 }], tax_note: '自定义税费说明' },
  })
  assert.deepEqual(withTiers.tiers, ['持有0–7天：1.50%'])
  assert.equal(withTiers.taxNote, '自定义税费说明')
})

test('research helpers', () => {
  const items = [
    { title: 'b', source: 's', published_at: '2026-10-20', symbol: '600519', kind: 'report' },
    { title: 'a', source: 's', published_at: '2026-10-21', symbol: '600519', kind: 'news' },
    { title: 'c', source: 's', published_at: '2026-10-19', symbol: '300750', url: 'https://example.com/x' },
  ]
  assert.deepEqual(researchSymbols(items), ['300750', '600519'])
  assert.equal(researchItemId(items[2]), 'https://example.com/x')
  assert.ok(researchItemId(items[0]).includes('b'))
  assert.deepEqual(sortResearchDesc(items).map(i => i.title), ['a', 'b', 'c'])
  assert.equal(filterResearchItems(items, '600519', 'all').length, 2)
  assert.equal(filterResearchItems(items, '600519', 'report').length, 1)
  assert.equal(filterResearchItems(items, '', 'news').length, 2)
  assert.equal(filterResearchItems(items, '300750', 'all').length, 1)
})

test('email settings validation', () => {
  assert.equal(validateEmailSettings({ host: 'smtp.x.com', port: 465, user: 'u', from: 'noreply@x.com' }), null)
  assert.ok(validateEmailSettings({ ...defaultEmailSettings(), from: 'noreply@x.com' }))
  assert.ok(validateEmailSettings({ host: 'smtp.x.com', port: 70000, user: 'u', from: 'noreply@x.com' }))
  assert.ok(validateEmailSettings({ host: 'smtp.x.com', port: 465, user: 'u', from: 'not-an-email' }))
  assert.equal(emailStatusName('sent'), '已发送')
  assert.equal(emailStatusName('weird'), 'weird')
})
