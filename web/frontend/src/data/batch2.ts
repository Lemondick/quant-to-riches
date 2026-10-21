/** Batch2 新页面的本地 mock 数据：后端联调前保证可构建、可演示。 */
import type { RebalanceState } from '../services/batch2Api'

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
