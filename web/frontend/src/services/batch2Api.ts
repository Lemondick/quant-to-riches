/**
 * Batch2 新页面 API 类型与纯函数。
 * 后端（流 A）并行开发中；路由按跨流契约 §3：
 *   api/v1/rebalance · api/v1/valuation · api/v1/funds/health ·
 *   api/v1/backtest/portfolio · api/v1/ask · api/v1/announcements
 * 联调前 Live 组件用本地 mock 数据保证可构建；mock 数据见 src/data/batch2.ts。
 */

export type RebalanceTarget = {
  equity_pct: number
  bond_pct: number
  cash_pct: number
  max_industry_pct: number
  deviation_threshold_pct: number
}

export type AllocationClass = 'equity' | 'bond' | 'cash'
export const ALLOCATION_CLASS_NAMES: Record<AllocationClass, string> = { equity: '股票类', bond: '债券类', cash: '现金类' }

export type AllocationSnapshot = {
  total_value: string
  classes: Record<AllocationClass, string>
  industries: { name: string; pct: string }[]
}

export type RebalanceState = {
  target: RebalanceTarget
  current: AllocationSnapshot
  as_of?: string | null
}

export type ClassAdvice = {
  class: AllocationClass
  name: string
  targetPct: number
  currentPct: number
  /** current - target，正数表示超配 */
  diffPct: number
  alert: boolean
  /** 建议卖出/买入 X 元；null 表示无需调仓 */
  suggestion: string | null
}

export type IndustryAlert = { name: string; pct: number; limit: number }

export function validateRebalanceTarget(target: RebalanceTarget): string | null {
  const parts = [target.equity_pct, target.bond_pct, target.cash_pct]
  if (parts.some(v => !Number.isFinite(v) || v < 0 || v > 100)) return '股/债/现金比例须在 0–100 之间。'
  if (Math.abs(parts[0] + parts[1] + parts[2] - 100) > 0.01) return '股/债/现金比例之和须为 100%。'
  if (!Number.isFinite(target.max_industry_pct) || target.max_industry_pct <= 0 || target.max_industry_pct > 100) return '行业集中度上限须在 0–100 之间。'
  if (!Number.isFinite(target.deviation_threshold_pct) || target.deviation_threshold_pct <= 0 || target.deviation_threshold_pct > 50) return '偏离阈值须在 0–50 之间。'
  return null
}

/** 由当前配置与目标配置算出偏离、告警与调仓建议（纯函数，可单元测试）。 */
export function buildRebalanceAdvice(state: RebalanceState): { advices: ClassAdvice[]; industryAlerts: IndustryAlert[]; hasAlert: boolean } {
  const total = Number(state.current.total_value) || 0
  const threshold = state.target.deviation_threshold_pct
  const advices: ClassAdvice[] = (Object.keys(ALLOCATION_CLASS_NAMES) as AllocationClass[]).map(cls => {
    const targetPct = state.target[`${cls}_pct`]
    const currentPct = Number(state.current.classes[cls]) || 0
    const diffPct = currentPct - targetPct
    const alert = Math.abs(diffPct) >= threshold
    let suggestion: string | null = null
    if (alert && total > 0) {
      const amount = Math.abs(diffPct) / 100 * total
      const formatted = `¥${Math.round(amount).toLocaleString('zh-CN')}`
      suggestion = diffPct > 0
        ? `建议卖出${ALLOCATION_CLASS_NAMES[cls]}约 ${formatted}（超配 ${diffPct.toFixed(1)}%）`
        : `建议买入${ALLOCATION_CLASS_NAMES[cls]}约 ${formatted}（低配 ${Math.abs(diffPct).toFixed(1)}%）`
    }
    return { class: cls, name: ALLOCATION_CLASS_NAMES[cls], targetPct, currentPct, diffPct, alert, suggestion }
  })
  const industryAlerts: IndustryAlert[] = state.current.industries
    .map(item => ({ name: item.name, pct: Number(item.pct) || 0, limit: state.target.max_industry_pct }))
    .filter(item => item.pct > item.limit)
  return { advices, industryAlerts, hasAlert: advices.some(a => a.alert) || industryAlerts.length > 0 }
}
