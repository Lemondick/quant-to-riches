import type { AccountReader } from './dataApi'

/**
 * Batch5 新页面 API 类型与纯函数（流 C）。
 * 路由：
 *   GET/POST api/v1/goals · GET/PUT/DELETE api/v1/goals/{id} · POST api/v1/goals/{id}/project
 *   GET api/v1/net-worth（?history_days=） · GET api/v1/net-worth/allocation
 *   GET api/v1/sentiment
 *   GET api/v1/dca/tracking · POST api/v1/dca/executions
 * Live 组件通过适配器读取账户数据；无数据或请求失败时显示空态与错误。
 */

/* ---------------- 目标规划（任务1） ---------------- */

/** 对应迁移 0048 goal_targets（字段与后端契约一致）。 */
export type GoalTarget = {
  id: string
  name: string
  target_amount: number
  target_date: string // yyyy-MM-dd
  current_amount: number
  assumed_annual_return: number // 如 0.06
  created_at?: string
  updated_at?: string
}

export type GoalProjection = {
  required_monthly: number
  projected_amount: number
  gap: number
  on_track: boolean
  assumptions: { monthly_amount?: number; assumed_annual_return: number; frequency: 'monthly' }
  /** 契约强制：必须醒目展示。 */
  disclaimer: string
  calculation_source?: string
  as_of_net_worth?: string
}

/** 契约强制文案（worker goal.project 亦返回此句）。 */
export const GOAL_DISCLAIMER = '假设测算非承诺'

/** 月数：从 today 起到 target_date 的整月数（不足 1 月按 1 月）。 */
export function monthsUntil(targetDate: string, today: Date = new Date()): number {
  const target = new Date(targetDate + 'T00:00:00')
  const now = new Date(today.getFullYear(), today.getMonth(), 1)
  if (Number.isNaN(target.getTime())) return 0
  let months = (target.getFullYear() - now.getFullYear()) * 12 + (target.getMonth() - now.getMonth())
  return Math.max(0, months)
}

/**
 * 本地兜底测算（联调前预览用）：按复利 + 等额月投推算。
 * 后端就绪后以 POST /goals/{id}/project（worker goal.project）的返回为准。
 */
export function projectGoalLocal(input: {
  target_amount: number
  target_date: string
  current_amount: number
  monthly_amount?: number
  assumed_annual_return: number
}): GoalProjection {
  const months = monthsUntil(input.target_date)
  const monthlyRate = Math.max(0, input.assumed_annual_return) / 12
  const n = Math.max(1, months)
  const futureCurrent = input.current_amount * Math.pow(1 + monthlyRate, n)
  const annuity = monthlyRate > 0
    ? (((Math.pow(1 + monthlyRate, n) - 1) / monthlyRate))
    : n
  const monthly = input.monthly_amount ?? 0
  const projected_amount = futureCurrent + monthly * annuity
  const gap = input.target_amount - projected_amount
  const required_monthly = input.monthly_amount === undefined
    ? Math.max(0, (input.target_amount - futureCurrent) / annuity)
    : monthly
  return {
    required_monthly,
    projected_amount,
    gap,
    on_track: gap <= 0,
    assumptions: { monthly_amount: input.monthly_amount, assumed_annual_return: input.assumed_annual_return, frequency: 'monthly' },
    disclaimer: GOAL_DISCLAIMER,
  }
}

/** 目标完成进度 0–100（current/target）。 */
export function goalProgress(goal: GoalTarget): number {
  if (!Number.isFinite(goal.target_amount) || goal.target_amount <= 0) return 0
  return Math.max(0, Math.min(100, (goal.current_amount / goal.target_amount) * 100))
}

export type GoalDeviationLevel = 'ontrack' | 'warn' | 'critical'
export const GOAL_DEVIATION_NAMES: Record<GoalDeviationLevel, string> = {
  ontrack: '进展正常', warn: '偏离预警', critical: '严重偏离',
}

/**
 * 偏离告警 badge：gap>0 且 on_track=false→偏离；逾期（target_date 已过且未达标）→严重；
 * 剩余时间不足且缺口超过目标 20%→严重。
 */
export function goalDeviation(goal: GoalTarget, projection: GoalProjection, today: Date = new Date()): GoalDeviationLevel {
  const overdue = goal.target_date < today.toISOString().slice(0, 10) && goal.current_amount < goal.target_amount
  if (overdue) return 'critical'
  if (!projection.on_track) {
    if (projection.gap > goal.target_amount * 0.2) return 'critical'
    return 'warn'
  }
  return 'ontrack'
}

/** 新建/编辑表单校验（金额、日期、年化）。 */
export function validateGoalForm(form: { name: string; target_amount: string; target_date: string; current_amount: string; assumed_annual_return: string }): string | null {
  if (!form.name.trim()) return '目标名称不能为空。'
  const target = Number(form.target_amount)
  if (!Number.isFinite(target) || target <= 0) return '目标金额须大于 0。'
  const current = Number(form.current_amount)
  if (form.current_amount !== '' && (!Number.isFinite(current) || current < 0)) return '当前金额不能为负数。'
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.target_date)) return '目标日期格式应为 yyyy-MM-dd。'
  const annual = Number(form.assumed_annual_return)
  if (form.assumed_annual_return !== '' && (!Number.isFinite(annual) || annual < 0 || annual > 1)) return '假设年化须在 0–1 之间（如 0.06）。'
  return null
}

/* ---------------- 净资产总览（任务2） ---------------- */

export type NetWorthAllocationClass = 'equity' | 'bond' | 'cash' | 'other'
export const NET_WORTH_CLASS_NAMES: Record<NetWorthAllocationClass, string> = {
  equity: '股票类', bond: '债券类', cash: '现金类', other: '其他',
}
export const NET_WORTH_CLASS_ORDER: NetWorthAllocationClass[] = ['equity', 'bond', 'cash', 'other']

export type NetWorthSnapshot = {
  /** 总资产（含负债口径的总资产）。 */
  total_assets: number
  /** 净资产（总资产 − 负债）。 */
  net_assets: number
  as_of: string | null
  liabilities_note?: string
  liabilities_recorded?: boolean
  allocation_items?: { name: string; value: number }[]
  /** 四类配置金额（股/债/现金/其他）。 */
  allocation: Record<NetWorthAllocationClass, number>
  /** 净资产历史曲线（?history_days=）。 */
  history: { date: string; net_assets: number }[]
}

export type DonutSlice = { name: string; value: string; pct: string; color: string; weight: number }
const ALLOCATION_COLORS: Record<NetWorthAllocationClass, string> = {
  equity: '#4d8dff', bond: '#38c172', cash: '#f5b942', other: '#9aa7bd',
}

/** 配置饼图：Donut 组件可用的切片。 */
export function allocationDonutItems(snapshot: NetWorthSnapshot, formatMoney: (value: number) => string): DonutSlice[] {
  if (snapshot.allocation_items) {
    const total = snapshot.allocation_items.reduce((sum, item) => sum + item.value, 0)
    const colors = Object.values(ALLOCATION_COLORS)
    return snapshot.allocation_items.map((item, index) => ({ name: item.name, value: formatMoney(item.value), pct: `${(total > 0 ? item.value / total * 100 : 0).toFixed(1)}%`, weight: total > 0 ? item.value / total * 100 : 0, color: colors[index % colors.length] }))
  }
  const total = NET_WORTH_CLASS_ORDER.reduce((sum, cls) => sum + (snapshot.allocation[cls] || 0), 0)
  return NET_WORTH_CLASS_ORDER.map(cls => {
    const amount = snapshot.allocation[cls] || 0
    const pct = total > 0 ? (amount / total) * 100 : 0
    return {
      name: NET_WORTH_CLASS_NAMES[cls],
      value: formatMoney(amount),
      pct: `${pct.toFixed(1)}%`,
      color: ALLOCATION_COLORS[cls],
      weight: pct,
    }
  })
}

/** 净资产历史曲线 → ChartCard 可用的相对涨跌幅序列。 */
export function netWorthObservations(snapshot: NetWorthSnapshot): { labels: string[]; series: { name: string; value: string; color: string; values: number[] }[] } {
  const base = snapshot.history[0]?.net_assets || 1
  const totalReturn = snapshot.history.length
    ? ((snapshot.history[snapshot.history.length - 1].net_assets / base - 1) * 100)
    : 0
  return {
    labels: snapshot.history.map(point => point.date.slice(5)),
    series: [{
      name: '净资产', color: '#38c172',
      value: (totalReturn >= 0 ? '+' : '') + totalReturn.toFixed(2) + '%',
      values: snapshot.history.map(point => +(((point.net_assets / base - 1) * 100).toFixed(3))),
    }],
  }
}

/* ---------------- 情绪指标（任务3） ---------------- */

/** 后端 GET /sentiment 的单项（worker sentiment.fetch 的 items 透传）。 */
export type SentimentItem = {
  name: string
  value: string
  unit: string
  updated_at: string
  source: string
}
export type SentimentSnapshot = { items: SentimentItem[]; warning?: string | null }

export const SENTIMENT_METRIC_ORDER = ['融资余额', '两市成交额', '换手率', '涨跌家数比']

/** 无数据（items 为空）时小组件展示 warning 空态。 */
export function sentimentEmptyReason(snapshot: SentimentSnapshot | null): string | null {
  if (!snapshot || !snapshot.items.length) return snapshot?.warning || '情绪数据源未接入，暂无情绪指标。'
  return null
}

/* ---------------- 定投跟踪（任务4） ---------------- */

export type DcaExecutionStatus = 'pending' | 'done' | 'missed' | 'skipped'
export const DCA_STATUS_NAMES: Record<DcaExecutionStatus, string> = {
  pending: '待扣款', done: '已扣款', missed: '漏投', skipped: '跳过',
}

export type DcaTrackingPeriod = {
  due_date: string // yyyy-MM-dd
  status: DcaExecutionStatus
  planned_amount: number
  actual_amount: number | null
  executed_at: string | null
  note: string | null
}

export type DcaTracking = {
  plan_id: string
  plan_name: string
  /** 漏投提醒阈值天数（QUANT_DCA_MISSED_ALERT_DAYS，后端配置）。 */
  missed_alert_days: number
  periods: DcaTrackingPeriod[]
}

export type DcaTrackingStats = {
  total_periods: number
  done_periods: number
  missed_periods: number
  pending_periods: number
  skipped_periods: number
  planned_total: number
  actual_total: number
  /** 金额偏离 = actual_total − planned_total（仅已扣款期）。 */
  amount_deviation: number
  execution_rate_pct: number
}

/** 计划 vs 执行对比统计（按期/漏投/金额偏离）。 */
export function dcaTrackingStats(tracking: DcaTracking): DcaTrackingStats {
  const stats: DcaTrackingStats = {
    total_periods: tracking.periods.length, done_periods: 0, missed_periods: 0,
    pending_periods: 0, skipped_periods: 0, planned_total: 0, actual_total: 0,
    amount_deviation: 0, execution_rate_pct: 0,
  }
  for (const period of tracking.periods) {
    stats[`${period.status}_periods` as 'done_periods' | 'missed_periods' | 'pending_periods' | 'skipped_periods']++
    if (period.status === 'done') {
      stats.planned_total += period.planned_amount
      stats.actual_total += period.actual_amount ?? 0
    }
  }
  stats.amount_deviation = stats.actual_total - stats.planned_total
  const executable = stats.done_periods + stats.missed_periods
  stats.execution_rate_pct = executable > 0 ? (stats.done_periods / executable) * 100 : 100
  return stats
}

/** 某期的金额偏离（已扣款期：实际 − 计划）。 */
export function dcaPeriodDeviation(period: DcaTrackingPeriod): number | null {
  if (period.status !== 'done' || period.actual_amount === null) return null
  return period.actual_amount - period.planned_amount
}

/** 登记扣款表单校验（POST /dca/executions 的 body）。 */
export function validateExecutionForm(form: { plan_id: string; due_date: string; actual_amount: string }): string | null {
  if (!form.plan_id) return '请选择定投计划。'
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.due_date)) return '扣款日期格式应为 yyyy-MM-dd。'
  const amount = Number(form.actual_amount)
  if (!Number.isFinite(amount) || amount <= 0) return '实际扣款金额须大于 0。'
  return null
}

export async function loadGoals(api: AccountReader, signal?: AbortSignal, monthlyAmounts: Record<string, number> = {}) {
  const { items } = await api.read<{ items: GoalTarget[] }>('/goals', signal)
  if (!Array.isArray(items)) throw new Error('目标数据格式不正确。')
  const goals = items.map(item => ({ ...item, target_amount: Number(item.target_amount), current_amount: Number(item.current_amount), assumed_annual_return: Number(item.assumed_annual_return) }))
  const projections: Record<string, GoalProjection> = {}, projectionErrors: string[] = []
  for (const goal of goals) {
    try {
      const monthly = monthlyAmounts[goal.id]
      const projection = await api.post<GoalProjection>(`/goals/${encodeURIComponent(goal.id)}/project`, monthly === undefined ? {} : { monthly_amount: monthly })
      projections[goal.id] = projection
      if (projection.as_of_net_worth !== undefined) goal.current_amount = Number(projection.as_of_net_worth)
    } catch (failure) {
      if (failure && typeof failure === 'object' && 'status' in failure && [401, 403].includes(Number(failure.status))) throw failure
      projectionErrors.push(`${goal.name}：${failure instanceof Error ? failure.message : '测算暂不可用。'}`)
    }
  }
  return { goals, projections, projectionErrors }
}
export async function loadNetWorth(api: AccountReader, signal?: AbortSignal): Promise<NetWorthSnapshot> {
  const [summary, allocation] = await Promise.all([
    api.read<{ total_assets: string; net_worth: string; as_of_date: string | null; liabilities_note: string; liabilities_recorded?: boolean; history: { date: string; total_value: string }[] }>('/net-worth?history_days=90', signal),
    api.read<{ items: { name: string; value: string }[] }>('/net-worth/allocation', signal),
  ])
  if (!Array.isArray(summary.history) || !Array.isArray(allocation.items)) throw new Error('净资产数据格式不正确。')
  return { total_assets: Number(summary.total_assets), net_assets: Number(summary.net_worth), as_of: summary.as_of_date, liabilities_note: summary.liabilities_note, liabilities_recorded: summary.liabilities_recorded === true,
    allocation: { equity: 0, bond: 0, cash: 0, other: 0 }, allocation_items: allocation.items.map(item => ({ name: item.name, value: Number(item.value) })),
    history: summary.history.map(item => ({ date: item.date, net_assets: Number(item.total_value) })) }
}
export async function loadSentiment(api: AccountReader, signal?: AbortSignal): Promise<SentimentSnapshot> {
  const { result } = await api.read<{ cached: boolean; result: SentimentSnapshot }>('/sentiment', signal)
  if (!Array.isArray(result?.items)) throw new Error('情绪数据格式不正确。')
  return result
}
export async function loadDcaTracking(api: AccountReader, signal?: AbortSignal): Promise<DcaTracking[]> {
  const result = await api.read<{ missed_alert_days: number; plans: { plan_id: string; symbol: string; frequency: string; periods: { due_date: string; state: 'on_time' | 'pending' | 'missed' | 'skipped'; planned_amount: string; actual_amount: string | null }[] }[] }>('/dca/tracking', signal)
  if (!Array.isArray(result.plans)) throw new Error('定投跟踪数据格式不正确。')
  return result.plans.map(plan => ({ plan_id: plan.plan_id, plan_name: `${plan.symbol} · ${plan.frequency === 'weekly' ? '每周' : '每月'}`, missed_alert_days: result.missed_alert_days,
    periods: plan.periods.map(period => ({ due_date: period.due_date, status: period.state === 'on_time' ? 'done' : period.state,
      planned_amount: Number(period.planned_amount), actual_amount: period.actual_amount === null ? null : Number(period.actual_amount), executed_at: null, note: null })) }))
}
