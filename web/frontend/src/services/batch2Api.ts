import type { AccountReader } from './dataApi'

/**
 * Batch2 新页面 API 类型与纯函数。
 * 路由：
 *   api/v1/rebalance/targets · api/v1/rebalance/evaluate · api/v1/valuation · api/v1/funds/health/latest ·
 *   api/v1/backtest/portfolio · api/v1/ask · api/v1/announcements
 * Live 组件通过本文件的适配器读取后端响应；演示数据仅用于演示页面。
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
  current: AllocationSnapshot | null
  as_of?: string | null
  configured?: boolean
  suggestions?: string[]
  breaches?: Partial<Record<AllocationClass, boolean>>
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
  temperature: { score: number | null; label: string }
  warnings?: string[]
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
  if (!state.current) return { advices: [], industryAlerts: [], hasAlert: false }
  const current = state.current
  const total = Number(current.total_value) || 0
  const threshold = state.target.deviation_threshold_pct
  const advices: ClassAdvice[] = (Object.keys(ALLOCATION_CLASS_NAMES) as AllocationClass[]).map(cls => {
    const targetPct = state.target[`${cls}_pct`]
    const currentPct = Number(current.classes[cls]) || 0
    const diffPct = currentPct - targetPct
    const alert = state.breaches?.[cls] ?? Math.abs(diffPct) > threshold
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
  const industryAlerts: IndustryAlert[] = current.industries
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
  xirr_pct: number | null
  max_drawdown_pct: number
  sharpe: number | null
  total_invested: number | null
  final_value: number | null
}

export type BacktestResult = {
  metrics: BacktestMetrics
  curve: { date: string; nav: number }[]
  rebalance_events: { date: string; action: string; detail?: string | null }[]
  warnings?: string[]
}

type PortfolioBacktestWire = {
  metrics: { total_return: number; xirr: number | null; max_drawdown: number; sharpe_zero_rate: number | null; total_invested: string; final_value: string }
  curve: { date: string; value: string; invested: string; unit_nav: number }[]
  rebalance_events: { date: string; action: string; reason: string }[]
  warnings: string[]
}
export function normalizePortfolioBacktest(result: PortfolioBacktestWire): BacktestResult {
  return {
    metrics: { total_return_pct: result.metrics.total_return * 100, xirr_pct: result.metrics.xirr === null ? null : result.metrics.xirr * 100,
      max_drawdown_pct: result.metrics.max_drawdown * 100, sharpe: result.metrics.sharpe_zero_rate,
      total_invested: Number(result.metrics.total_invested), final_value: Number(result.metrics.final_value) },
    curve: result.curve.map(point => ({ date: point.date, nav: point.unit_nav })),
    rebalance_events: result.rebalance_events.map(event => ({ ...event, detail: event.reason })), warnings: result.warnings,
  }
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

/* ---------------- 自然语言问答（任务7） ---------------- */

export type AskResponse = {
  answer: string
  matched_template?: string | null
  cost_note?: string | null
}

/** 10 个高频问题快捷 chips（与后端模板对齐，契约§5）。 */
export const ASK_QUICK_QUESTIONS = [
  '今年收益', '盈亏排名', '回撤贡献', '定投统计', '费率支出',
  '现金比例', '行业分布', '近一月涨跌', '分红统计', '估值分位',
]

/* ---------------- 公告流（任务8） ---------------- */

export type AnnouncementKind = 'dividend' | 'manager_change' | 'liquidation_risk' | 'other'
export const ANNOUNCEMENT_KIND_NAMES: Record<AnnouncementKind, string> = {
  dividend: '分红', manager_change: '经理变更', liquidation_risk: '清盘预警', other: '其他',
}

export type Announcement = {
  id?: string
  fund_code: string
  fund_name?: string | null
  title: string
  kind: AnnouncementKind
  published_at: string
  source_url?: string | null
  summary?: string | null
}

export type AnnouncementList = { announcements: Announcement[]; total?: number }

/** 公告唯一键：后端未给 id 时用三元组。 */
export function announcementId(item: Announcement): string {
  return item.id ?? `${item.fund_code}|${item.published_at}|${item.title}`
}

/** 按发布时间倒序。 */
export function sortAnnouncementsDesc(items: Announcement[]): Announcement[] {
  return [...items].sort((a, b) => b.published_at.localeCompare(a.published_at))
}

const READ_KEY = 'guanlan.announcements.read.v1'

/** 已读 id 集合（本地记录，localStorage）。 */
export function loadReadAnnouncementIds(): Set<string> {
  try {
    const raw = localStorage.getItem(READ_KEY)
    if (!raw) return new Set()
    const parsed: unknown = JSON.parse(raw)
    return new Set(Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : [])
  } catch {
    return new Set()
  }
}

export function saveReadAnnouncementIds(ids: Set<string>): void {
  try {
    localStorage.setItem(READ_KEY, JSON.stringify([...ids].slice(-500)))
  } catch {
    /* 本次会话内仍可用内存集合，见调用方。 */
  }
}

type RebalanceTargetsWire = { stock_ratio: string; bond_ratio: string; cash_ratio: string; industry_concentration_limit: string; deviation_threshold: string; updated_at: string }
function rebalanceTarget(target: RebalanceTargetsWire): RebalanceTarget {
  return { equity_pct: Number(target.stock_ratio) * 100, bond_pct: Number(target.bond_ratio) * 100, cash_pct: Number(target.cash_ratio) * 100,
    max_industry_pct: Number(target.industry_concentration_limit) * 100, deviation_threshold_pct: Number(target.deviation_threshold) * 100 }
}
export function rebalanceTargetBody(target: RebalanceTarget) {
  return { stock_ratio: target.equity_pct / 100, bond_ratio: target.bond_pct / 100, cash_ratio: target.cash_pct / 100,
    industry_concentration_limit: target.max_industry_pct / 100, deviation_threshold: target.deviation_threshold_pct / 100 }
}
export async function loadRebalance(api: AccountReader, signal?: AbortSignal): Promise<RebalanceState> {
  const { targets } = await api.read<{ targets: RebalanceTargetsWire | null }>('/rebalance/targets', signal)
  if (!targets) return { configured: false, target: { equity_pct: 0, bond_pct: 0, cash_pct: 0, max_industry_pct: 30, deviation_threshold_pct: 5 }, current: null }
  const result = await api.post<{ as_of: string; total_value: string; targets: RebalanceTargetsWire; allocation: { stock: string; bond: string; cash: string }; industry: { top: string | null; ratio: string }; drifts: { asset_class: 'stock' | 'bond' | 'cash'; breached: boolean }[]; suggestions: { note: string }[] }>('/rebalance/evaluate')
  const total = Number(result.total_value)
  const pct = (value: string) => String(total > 0 ? Number(value) / total * 100 : 0)
  return { configured: true, target: rebalanceTarget(result.targets), as_of: result.as_of, current: { total_value: result.total_value,
    classes: { equity: pct(result.allocation.stock), bond: pct(result.allocation.bond), cash: pct(result.allocation.cash) },
    industries: result.industry.top ? [{ name: result.industry.top, pct: String(Number(result.industry.ratio) * 100) }] : [] },
    suggestions: result.suggestions.map(item => item.note),
    breaches: { equity: false, bond: false, cash: false, ...Object.fromEntries(result.drifts.map(item => [item.asset_class === 'stock' ? 'equity' : item.asset_class, item.breached])) } }
}
export async function loadValuation(api: AccountReader, signal?: AbortSignal): Promise<ValuationSnapshot> {
  const { result } = await api.read<{ cached: boolean; result: ValuationSnapshot }>('/valuation', signal)
  if (!Array.isArray(result?.items) || !result.temperature) throw new Error('估值数据格式不正确。')
  return { ...result, items: result.items.map(item => ({ ...item, name: item.name || VALUATION_INDEX_NAMES[item.symbol] || VALUATION_INDEX_NAMES[`${item.symbol}.SH`] || VALUATION_INDEX_NAMES[`${item.symbol}.SZ`] || item.symbol, erp: item.erp === null ? null : Number(item.erp) * 100 })) }
}
export async function loadFundHealth(api: AccountReader, signal?: AbortSignal): Promise<FundHealthSnapshot | null> {
  const { report } = await api.read<{ report: FundHealthSnapshot | null; cron: string }>('/funds/health/latest', signal)
  if (report !== null && !Array.isArray(report?.reports)) throw new Error('基金体检数据格式不正确。')
  return report
}
export async function loadAnnouncements(api: AccountReader, signal?: AbortSignal): Promise<AnnouncementList> {
  const result = await api.read<{ items: (Omit<Announcement, 'published_at'> & { published_at: string | null })[]; total: number }>('/announcements?limit=100', signal)
  if (!Array.isArray(result.items)) throw new Error('公告数据格式不正确。')
  return { announcements: result.items.map(item => ({ ...item, published_at: item.published_at ?? '' })), total: result.total }
}
