import type { AccountReader } from './dataApi'

/**
 * Batch3 新页面 API 类型与纯函数（流 C）。
 * 后端（流 A）并行开发中；路由按跨流契约 §3：
 *   GET/POST api/v1/risk/metrics · GET api/v1/risk/stress
 *   GET api/v1/cost/positions
 *   GET api/v1/research · POST api/v1/research/refresh
 *   GET/PUT api/v1/notify/email/settings · GET api/v1/notify/email/log
 * 铁律：不虚构数字；后端未就绪时前端展示空态+说明，不使用本地假数据。
 */

/* ---------------- 风险看板（任务1） ---------------- */

export type RiskNavPoint = { date: string; nav: number }

export type RiskMetrics = {
  /** 日 VaR（95%，历史模拟法）：未来一日最大可能损失占组合净值的比例，负数表示损失。 */
  var95: number
  /** 区间最大回撤（比例，如 -0.18 表示 -18%）。 */
  max_drawdown: number
  /** 年化波动率（比例）。 */
  ann_volatility: number
  /** 夏普比率（null 表示暂无无风险利率或区间过短无法计算）。 */
  sharpe: number | null
  /** 卡玛比率 = 年化收益 / 最大回撤绝对值（null 表示暂无）。 */
  calmar: number | null
  period_days: number
  /** 后端声明的计算方法原文，前端方法论说明围绕它展开。 */
  method: string
  computed_at: string
  /** 净值序列（可选）：后端若不提供，净值曲线区展示空态。 */
  nav_series?: RiskNavPoint[] | null
}

export type DrawdownPoint = { date: string; nav: number; drawdown_pct: number }

/** 由净值序列计算每点回撤（相对滚动峰值的百分比，负数或 0）。 */
export function computeDrawdown(series: RiskNavPoint[]): DrawdownPoint[] {
  let peak = 0
  return series.map(point => {
    if (!Number.isFinite(point.nav) || point.nav <= 0) return { date: point.date, nav: point.nav, drawdown_pct: 0 }
    peak = Math.max(peak, point.nav)
    return { date: point.date, nav: point.nav, drawdown_pct: peak > 0 ? (point.nav / peak - 1) * 100 : 0 }
  })
}

/** 风险指标格式化：比例→百分比字符串，null/NaN → '—'。 */
export function fmtRiskPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/** 无量纲指标格式化（如夏普/卡玛），null/NaN → '—'。 */
export function fmtRiskRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(2)
}

/** 方法论说明：以后端 method 字段为准的固定释义模板。 */
export function riskMethodNote(method: string): string {
  return `后端计算口径：${method || '（暂未声明）'}。VaR（历史模拟法，95%）指基于历史收益率分布，在未来一日内有 95% 把握损失不超过该比例；最大回撤为区间内净值从峰值跌至谷底的最大跌幅；年化波动率刻画净值波动剧烈程度；夏普比率衡量单位波动换来的超额收益；卡玛比率衡量单位回撤换来的年化收益。`
}

/* ---------------- 压力测试（/risk 内嵌 tab） ---------------- */

export type StressScenario = {
  name: string
  /** 该情景下宽基指数区间跌幅（%），正数表示下跌幅度。 */
  index_drop_pct: number
  /** 组合假想损失（%），正数表示损失比例。 */
  est_loss_pct: number
  /** 组合假想损失金额（null 表示暂无金额测算）。 */
  est_loss_amount: number | null
}

export type StressTestResult = {
  scenarios: StressScenario[]
  /** 后端返回的免责声明原文（可能为 null）。 */
  disclaimer: string | null
}

/** 必须醒目展示的常量声明（契约要求：假想测算非预测）。 */
export const STRESS_DISCLAIMER_FALLBACK =
  '假想测算非预测：压力测试基于历史极端情景的假想跌幅测算组合损失，不构成对未来收益的预测，不作为投资建议。'

/** 某情景条形图宽度（相对最大假想损失的百分比，用于横向条形图）。 */
export function stressBarWidth(scenario: StressScenario, maxLossPct: number): number {
  if (!Number.isFinite(scenario.est_loss_pct) || maxLossPct <= 0) return 0
  return Math.max(0, Math.min(100, (scenario.est_loss_pct / maxLossPct) * 100))
}

/** 三情景中的最大假想损失（条形图归一化基准）。 */
export function maxStressLoss(scenarios: StressScenario[]): number {
  return scenarios.reduce((max, item) => Math.max(max, Number.isFinite(item.est_loss_pct) ? item.est_loss_pct : 0), 0)
}

/* ---------------- 持有成本（任务3） ---------------- */

export type RedemptionTier = {
  /** 持有天数下限（含）。 */
  min_days: number
  /** 持有天数上限（含），null 表示无上限。 */
  max_days: number | null
  /** 费率（比例，如 0.015 表示 1.5%）。 */
  rate: number
}

export type CostPosition = {
  symbol: string
  name?: string | null
  /** 持仓总成本（元）。 */
  cost_total: string
  /** 最新价/单位净值。 */
  price: string | null
  /** 浮动盈亏（元）。 */
  unrealized_pnl: string
  /** 浮动盈亏率（比例）。 */
  unrealized_pnl_rate: string | null
  /** 年化收益率（比例，null 表示区间不足或无法计算）。 */
  annualized_return: string | null
  /** 回本线价格（null 表示暂无）。 */
  breakeven_price: string | null
  /** 持有天数。 */
  holding_days: number | null
  /** 赎回提示（后端按 FundFeeRules 口径返回；缺失时只展示通用税费提醒）。 */
  redemption?: {
    fee_tiers: RedemptionTier[]
    /** 后端返回的税费提示原文；缺失时前端展示通用提醒。 */
    tax_note?: string | null
    estimate?: { rate: number | null; fee: number | null; basis: string }
  } | null
}

export type CostSnapshot = { as_of: string | null; positions: CostPosition[] }

/** 赎回费率档位文字（如 "持有≥7天且<30天：1.50%"）。 */
export function redemptionTierText(tier: RedemptionTier): string {
  const rate = `${(tier.rate * 100).toFixed(2)}%`
  const range = tier.max_days === null
    ? `持有≥${tier.min_days}天`
    : `持有${tier.min_days}–${tier.max_days}天`
  return `${range}：${rate}`
}

/** 税费通用提示（不虚构具体税率数字，仅提醒口径归属）。 */
export const COST_TAX_NOTE_FALLBACK =
  '税费提醒：赎回费按持有天数档位计提并归入基金资产（以基金合同为准）；场内/场外交易税费以券商结算单为准，具体以现行税收政策与基金公告为准。'

/** 行展开用的赎回提示文本：档位 + 税费说明。 */
export function redemptionHintText(position: CostPosition): { tiers: string[]; taxNote: string } {
  const tiers = (position.redemption?.fee_tiers ?? []).map(redemptionTierText)
  const estimate = position.redemption?.estimate
  if (estimate?.rate !== null && estimate?.rate !== undefined) tiers.push(`当前适用赎回费率：${(estimate.rate * 100).toFixed(2)}%；估算赎回费 ${estimate.fee === null ? '暂无' : '¥' + estimate.fee.toFixed(2)}（${estimate.basis}）`)
  else if (estimate?.basis) tiers.push(estimate.basis)
  return { tiers, taxNote: position.redemption?.tax_note || COST_TAX_NOTE_FALLBACK }
}

export async function loadRiskMetrics(api: AccountReader, signal?: AbortSignal): Promise<RiskMetrics> {
  const { result } = await api.read<{ cached: boolean; result: RiskMetrics }>('/risk/metrics', signal)
  if (!result || !('var95' in result)) throw new Error('风险数据格式不正确。')
  return result
}
export async function loadStressTest(api: AccountReader, signal?: AbortSignal): Promise<StressTestResult> {
  const result = await api.read<StressTestResult>('/risk/stress', signal)
  if (!Array.isArray(result.scenarios)) throw new Error('压力测试数据格式不正确。')
  return { ...result, scenarios: result.scenarios.map(item => ({ ...item, index_drop_pct: Math.abs(item.index_drop_pct) * 100,
    est_loss_pct: Math.abs(item.est_loss_pct) * 100, est_loss_amount: item.est_loss_amount === null ? null : Math.abs(item.est_loss_amount) })) }
}
type CostWire = { symbol: string; name: string | null; quantity: number; total_cost: number; market_value: number; floating_pnl: number; pnl_rate: number | null; annualized_return: number | null; weighted_holding_days: number | null; break_even_nav: number | null; redemption_rate: number | null; redemption_fee_estimate: number | null; fee_basis: string }
export async function loadCost(api: AccountReader, signal?: AbortSignal): Promise<CostSnapshot> {
  const result = await api.read<{ items: CostWire[]; tax_note: string }>('/cost/positions', signal)
  if (!Array.isArray(result.items)) throw new Error('持有成本数据格式不正确。')
  const text = (value: number | null) => value === null ? null : String(value)
  return { as_of: null, positions: result.items.map(item => ({ symbol: item.symbol, name: item.name, cost_total: String(item.total_cost),
    price: item.quantity > 0 ? String(item.market_value / item.quantity) : null, unrealized_pnl: String(item.floating_pnl), unrealized_pnl_rate: text(item.pnl_rate),
    annualized_return: text(item.annualized_return), holding_days: item.weighted_holding_days, breakeven_price: text(item.break_even_nav),
    redemption: { fee_tiers: [], tax_note: result.tax_note, estimate: { rate: item.redemption_rate, fee: item.redemption_fee_estimate, basis: item.fee_basis } } })) }
}

/* ---------------- 研报资讯（任务4） ---------------- */

export type ResearchKind = 'report' | 'news'
export const RESEARCH_KIND_NAMES: Record<ResearchKind, string> = { report: '研报', news: '资讯' }

export type ResearchItem = {
  title: string
  summary?: string | null
  source: string
  url?: string | null
  published_at: string
  symbol?: string | null
  /** report=研报，news=资讯；后端未给时归入"资讯"展示但保留原始缺失标记。 */
  kind?: ResearchKind | null
}

export type ResearchList = { items: ResearchItem[]; fetched_at?: string | null }

/** 研报唯一键：url 优先，否则三元组。 */
export function researchItemId(item: ResearchItem): string {
  return item.url || `${item.symbol ?? ''}|${item.published_at}|${item.title}`
}

/** 按发布时间倒序。 */
export function sortResearchDesc(items: ResearchItem[]): ResearchItem[] {
  return [...items].sort((a, b) => b.published_at.localeCompare(a.published_at))
}

/** 数据中出现过的标的列表（用于 symbol 筛选下拉）。 */
export function researchSymbols(items: ResearchItem[]): string[] {
  return [...new Set(items.map(item => (item.symbol ?? '').trim()).filter(Boolean))].sort()
}

/** 按 symbol 与 kind 筛选。 */
export function filterResearchItems(items: ResearchItem[], symbol: string, kind: 'all' | ResearchKind): ResearchItem[] {
  return items.filter(item =>
    (!symbol || (item.symbol ?? '') === symbol) &&
    (kind === 'all' || (item.kind ?? 'news') === kind))
}

/* ---------------- 邮件通知（任务5） ---------------- */

export type EmailSettings = {
  host: string
  port: number
  user: string
  /** 发件人地址。 */
  from: string
}

export type EmailLogEntry = {
  id: string
  user_id?: string | null
  to_addr: string
  subject: string
  template: string
  status: string
  attempts: number
  last_error?: string | null
  created_at: string
}

export const EMAIL_LOG_STATUS_NAMES: Record<string, string> = {
  sent: '已发送', failed: '失败', pending: '待发送', retrying: '重试中',
}

export function emailStatusName(status: string): string {
  return EMAIL_LOG_STATUS_NAMES[status] ?? status
}

export function validateEmailSettings(settings: EmailSettings): string | null {
  if (!settings.host.trim()) return 'SMTP 服务器地址不能为空。'
  if (!Number.isInteger(settings.port) || settings.port <= 0 || settings.port > 65535) return 'SMTP 端口须为 1–65535 的整数。'
  if (!settings.user.trim()) return 'SMTP 用户名不能为空。'
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(settings.from.trim())) return '发件人地址格式无效。'
  return null
}

/** 默认 SMTP 配置（展示空表单用；不含密码）。 */
export function defaultEmailSettings(): EmailSettings {
  return { host: '', port: 465, user: '', from: '' }
}
