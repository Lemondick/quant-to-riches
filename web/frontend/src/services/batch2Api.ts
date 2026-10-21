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
/* ---------------- 估值仪表盘（任务2） ---------------- */

export type ValuationItem = {
  symbol: string
  name: string
  pe: number | null
  pb: number | null
  /** PE 百分位 0–100，越低越便宜 */
  pe_pct: number
  pb_pct: number
  /** 股权风险溢价（%），null 表示暂无 */
  erp: number | null
}

export type ValuationSnapshot = {
  as_of: string
  items: ValuationItem[]
  temperature: { score: number; label: string }
}

export const VALUATION_INDEX_ORDER = ['000300.SH', '000905.SH', '399006.SZ', 'HSI', 'SPX', 'NDX']
export const VALUATION_INDEX_NAMES: Record<string, string> = {
  '000300.SH': '沪深300', '000905.SH': '中证500', '399006.SZ': '创业板指',
  HSI: '恒生', SPX: '标普500', NDX: '纳斯达克',
}

/** 市场温度文案兜底（后端未给 label 时使用）。 */
export function temperatureLabel(score: number): string {
  if (score < 30) return '低估'
  if (score < 45) return '偏低'
  if (score < 55) return '合理'
  if (score < 70) return '偏高'
  return '高估'
}

/** 百分位条颜色：越低（越便宜）越绿，越高越红。 */
export function percentileTone(pct: number): string {
  if (pct < 30) return 'low'
  if (pct < 70) return 'mid'
  return 'high'
}

/* ---------------- 基金体检（任务4） ---------------- */

export type HealthSeverity = 'info' | 'warn' | 'critical'
export type HealthKind = 'scale_change' | 'manager_change' | 'style_drift' | 'fee_level'
export const HEALTH_KIND_NAMES: Record<HealthKind, string> = {
  scale_change: '规模', manager_change: '经理', style_drift: '风格', fee_level: '费率',
}
export const HEALTH_KIND_ORDER: HealthKind[] = ['scale_change', 'manager_change', 'style_drift', 'fee_level']
export const HEALTH_SEVERITY_NAMES: Record<HealthSeverity, string> = { info: '提示', warn: '关注', critical: '严重' }

export type HealthFinding = {
  kind: HealthKind
  severity: HealthSeverity
  detail: string
  evidence?: string | null
  /** 数据不足时后端可置 true，前端明确标注 */
  insufficient?: boolean
}

export type FundHealthReport = {
  code: string
  name?: string | null
  findings: HealthFinding[]
}

export type FundHealthSnapshot = {
  as_of: string
  reports: FundHealthReport[]
}

/** 是否为"数据不足"类发现：显式标记或文案含"数据不足"。 */
export function isInsufficient(finding: HealthFinding): boolean {
  return finding.insufficient === true || finding.detail.includes('数据不足')
}

/** 按四类顺序整理某只基金的发现（缺失的类别不占位）。 */
export function orderFindings(report: FundHealthReport): HealthFinding[] {
  const rank = new Map(HEALTH_KIND_ORDER.map((kind, index) => [kind, index]))
  return [...report.findings].sort((a, b) => (rank.get(a.kind) ?? 99) - (rank.get(b.kind) ?? 99))
}

/** 某只基金的最高严重度（用于分组标题徽标）。 */
export function worstSeverity(report: FundHealthReport): HealthSeverity {
  if (report.findings.some(f => f.severity === 'critical')) return 'critical'
  if (report.findings.some(f => f.severity === 'warn')) return 'warn'
  return 'info'
}

/** severity 排序权重（critical 优先展示）。 */
export function severityRank(severity: HealthSeverity): number {
  return { critical: 0, warn: 1, info: 2 }[severity]
}
export function cheapestIndex(snapshot: ValuationSnapshot): ValuationItem | null {
  if (!snapshot.items.length) return null
  const score = (item: ValuationItem) => (item.pe_pct + item.pb_pct) / 2
  return [...snapshot.items].sort((a, b) => score(a) - score(b) || a.pe_pct - b.pe_pct)[0]
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

/* ---------------- 组合回测（任务6） ---------------- */

export type BacktestFundWeight = { code: string; name?: string; weight: number }
export type BacktestRebalanceRule =
  | { mode: 'period'; period: '1M' | '3M' }
  | { mode: 'threshold'; threshold_pct: number }
export type BacktestDca = { amount: number; freq: 'monthly' | 'weekly' }
export type BacktestRequest = {
  funds: BacktestFundWeight[]
  start: string
  end: string
  rebalance: BacktestRebalanceRule
  dca?: BacktestDca
}

export type BacktestMetrics = {
  total_return_pct: number
  xirr_pct: number
  max_drawdown_pct: number
  sharpe: number | null
  total_invested: number | null
  final_value: number | null
}

export type BacktestResult = {
  metrics: BacktestMetrics
  curve: { date: string; nav: number }[]
  rebalance_events: { date: string; action: string; detail?: string | null }[]
}

export function validateBacktestRequest(request: BacktestRequest): string | null {
  if (!request.funds.length) return '请至少配置一只基金。'
  if (request.funds.some(f => !f.code.trim())) return '基金代码不能为空。'
  if (request.funds.some(f => !Number.isFinite(f.weight) || f.weight <= 0 || f.weight > 100)) return '权重须在 0–100 之间。'
  const codes = request.funds.map(f => f.code.trim())
  if (new Set(codes).size !== codes.length) return '基金代码不能重复。'
  if (Math.abs(request.funds.reduce((sum, f) => sum + f.weight, 0) - 100) > 0.01) return '权重之和须为 100%。'
  if (!request.start || !request.end || request.start >= request.end) return '起止日期无效（开始须早于结束）。'
  if (request.rebalance.mode === 'threshold') {
    const pct = (request.rebalance as { threshold_pct: number }).threshold_pct
    if (!Number.isFinite(pct) || pct <= 0 || pct > 50) return '偏离阈值须在 0–50 之间。'
  }
  if (request.dca && (!Number.isFinite(request.dca.amount) || request.dca.amount <= 0)) return '定投金额须大于 0。'
  return null
}

/** 净值曲线 → ChartCard 可用的相对涨跌幅序列。 */
export function backtestCurveObservations(result: BacktestResult): { labels: string[]; series: { name: string; value: string; color: string; values: number[] }[] } {
  const base = result.curve[0]?.nav || 1
  return {
    labels: result.curve.map(p => p.date.slice(5)),
    series: [{
      name: '组合净值', color: '#4d8dff',
      value: (result.metrics.total_return_pct >= 0 ? '+' : '') + result.metrics.total_return_pct.toFixed(2) + '%',
      values: result.curve.map(p => +(((p.nav / base - 1) * 100).toFixed(3))),
    }],
  }
}
