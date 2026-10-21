/** Batch2 新页面的本地 mock 数据：后端联调前保证可构建、可演示。 */
import type { RebalanceState, ValuationSnapshot, FundHealthSnapshot } from '../services/batch2Api'

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

export function mockFundHealth(): FundHealthSnapshot {
  return {
    as_of: '2026-10-21',
    reports: [
      {
        code: '110022', name: '易方达消费行业股票',
        findings: [
          { kind: 'scale_change', severity: 'warn', detail: '近一季度规模缩水 18%，跌破 50 亿元关口。', evidence: '2026Q3: 41.2亿 / 2026Q2: 50.3亿' },
          { kind: 'manager_change', severity: 'info', detail: '现任基金经理任职 4.2 年，任职回报跑赢基准 12.6%。', evidence: '任职起始 2022-06' },
          { kind: 'style_drift', severity: 'info', detail: '持仓风格稳定，大盘成长占比 72%。' },
          { kind: 'fee_level', severity: 'warn', detail: '管理费 1.50%/年，高于同类中位数 1.20%。', evidence: '同类中位数 1.20%' },
        ],
      },
      {
        code: '005911', name: '广发双擎升级混合',
        findings: [
          { kind: 'scale_change', severity: 'critical', detail: '规模连续两季度低于 5000 万元清盘线，存在清盘风险。', evidence: '2026Q3: 0.42亿' },
          { kind: 'manager_change', severity: 'warn', detail: '基金经理 2026-08 发生变更，新任经理尚无完整年度业绩。', evidence: '变更公告 2026-08-15' },
          { kind: 'style_drift', severity: 'warn', detail: '前十大重仓中新能源占比由 10% 升至 35%，偏离契约约定的均衡风格。' },
          { kind: 'fee_level', severity: 'info', detail: '管理费 1.50%/年，与同类持平。' },
        ],
      },
      {
        code: '270002', name: '广发稳健增长混合',
        findings: [
          { kind: 'scale_change', severity: 'info', detail: '数据不足：该基金近两季度规模数据缺失，无法评估规模变化。', insufficient: true },
          { kind: 'manager_change', severity: 'info', detail: '基金经理任职 6.8 年，无变更记录。' },
          { kind: 'style_drift', severity: 'info', detail: '数据不足：缺少持仓明细，无法判断风格漂移。', insufficient: true },
          { kind: 'fee_level', severity: 'info', detail: '管理费 1.50%/年。' },
        ],
      },
    ],
  }
}
