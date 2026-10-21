/** Batch2 新页面的本地 mock 数据：后端联调前保证可构建、可演示。 */
import type { RebalanceState, ValuationSnapshot } from '../services/batch2Api'

export function mockRebalanceState(): RebalanceState {
  return {
    as_of: '2026-10-21',
    target: { equity_pct: 60, bond_pct: 25, cash_pct: 15, max_industry_pct: 20, deviation_threshold_pct: 3 },
    current: {
      total_value: '1250000',
      classes: { equity: '64.8', bond: '22.1', cash: '13.1' },
      industries: [
        { name: '白酒', pct: '18.5' },
        { name: '银行', pct: '12.2' },
        { name: '医药', pct: '9.8' },
        { name: '新能源', pct: '8.4' },
      ],
    },
  }
}

export function mockValuation(): ValuationSnapshot {
  return {
    as_of: '2026-10-21',
    temperature: { score: 42, label: '偏低' },
    items: [
      { symbol: '000300.SH', name: '沪深300', pe: 12.4, pb: 1.42, pe_pct: 28, pb_pct: 31, erp: 4.6 },
      { symbol: '000905.SH', name: '中证500', pe: 24.8, pb: 1.96, pe_pct: 52, pb_pct: 48, erp: 3.1 },
      { symbol: '399006.SZ', name: '创业板指', pe: 31.2, pb: 3.88, pe_pct: 64, pb_pct: 58, erp: 2.4 },
      { symbol: 'HSI', name: '恒生', pe: 9.8, pb: 0.98, pe_pct: 18, pb_pct: 15, erp: 6.2 },
      { symbol: 'SPX', name: '标普500', pe: 28.6, pb: 4.72, pe_pct: 82, pb_pct: 88, erp: 1.2 },
      { symbol: 'NDX', name: '纳斯达克', pe: 34.1, pb: 6.05, pe_pct: 90, pb_pct: 93, erp: 0.8 },
    ],
  }
}
