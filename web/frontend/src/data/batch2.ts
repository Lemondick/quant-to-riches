/** Batch2 新页面的本地 mock 数据：后端联调前保证可构建、可演示。 */
import type { RebalanceState, ValuationSnapshot, FundHealthSnapshot, BacktestRequest, BacktestResult, AnnouncementList } from '../services/batch2Api'
import { ASK_QUICK_QUESTIONS } from '../services/batch2Api.ts'

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

/** 确定性伪随机（mock 回测曲线用，同参数同结果）。 */
function seededRandom(seed: string): () => number {
  let hash = 2166136261
  for (let i = 0; i < seed.length; i++) { hash ^= seed.charCodeAt(i); hash = Math.imul(hash, 16777619) }
  let state = hash >>> 0
  return () => {
    state |= 0; state = (state + 0x6d2b79f5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function mockBacktestFunds(): { code: string; name: string; weight: number }[] {
  return [
    { code: '110022', name: '易方达消费行业股票', weight: 40 },
    { code: '005911', name: '广发双擎升级混合', weight: 35 },
    { code: '270002', name: '广发稳健增长混合', weight: 25 },
  ]
}

/** 演示用回测结果：按请求参数确定性生成曲线/事件/指标。 */
export function mockBacktestResult(request: BacktestRequest): BacktestResult {
  const random = seededRandom(JSON.stringify(request))
  const start = new Date(request.start + 'T00:00:00')
  const end = new Date(request.end + 'T00:00:00')
  const days = Math.max(30, Math.min(730, Math.round((end.getTime() - start.getTime()) / 86400000)))
  const step = Math.max(1, Math.ceil(days / 260))
  const drift = 0.0004 + random() * 0.0006
  const vol = 0.008 + random() * 0.008
  const curve: { date: string; nav: number }[] = []
  let nav = 1
  let peak = 1, maxDrawdown = 0
  for (let d = 0; d <= days; d += step) {
    if (d > 0) {
      nav *= 1 + drift + (random() - 0.48) * vol
      peak = Math.max(peak, nav)
      maxDrawdown = Math.min(maxDrawdown, nav / peak - 1)
    }
    const date = new Date(start.getTime() + d * 86400000)
    curve.push({ date: date.toISOString().slice(0, 10), nav: +nav.toFixed(4) })
  }
  const totalReturn = (nav - 1) * 100
  const years = days / 365
  const events: BacktestResult['rebalance_events'] = []
  if (request.rebalance.mode === 'period') {
    const months = request.rebalance.period === '1M' ? 1 : 3
    for (let m = months; m * 30 < days; m += months) {
      const date = new Date(start.getTime() + m * 30 * 86400000)
      events.push({ date: date.toISOString().slice(0, 10), action: '再平衡', detail: `按${request.rebalance.period}周期恢复目标权重` })
    }
  } else {
    const threshold = (request.rebalance as { threshold_pct: number }).threshold_pct
    for (let i = 0; i < 4; i++) {
      const date = new Date(start.getTime() + Math.round(random() * days) * 86400000)
      events.push({ date: date.toISOString().slice(0, 10), action: '再平衡', detail: `组合偏离超过阈值 ${threshold}% 触发再平衡` })
    }
    events.sort((a, b) => a.date.localeCompare(b.date))
  }
  const invested = request.dca ? request.dca.amount * Math.round(days / (request.dca.freq === 'monthly' ? 30 : 7)) : null
  return {
    metrics: {
      total_return_pct: +totalReturn.toFixed(2),
      xirr_pct: +((Math.pow(nav, 1 / years) - 1) * 100).toFixed(2),
      max_drawdown_pct: +(maxDrawdown * 100).toFixed(2),
      sharpe: +(0.6 + random() * 0.9).toFixed(2),
      total_invested: invested,
      final_value: invested === null ? null : +(invested * nav).toFixed(2),
    },
    curve,
    rebalance_events: events,
  }
}

/** 演示模式问答：10 个高频问题的本地答案（明确标注为演示）。 */
export function mockAskAnswer(question: string): string {
  const answers: Record<string, string> = {
    '今年收益': '【演示】今年以来组合累计收益 +8.42%，跑赢沪深300（+3.17%）5.25 个百分点。',
    '盈亏排名': '【演示】盈利前三：易方达消费行业（+12,400元）、沪深300ETF（+8,210元）、招商银行（+5,930元）；亏损最大：某新能源基金（-6,120元）。',
    '回撤贡献': '【演示】本轮最大回撤 -9.6% 中，新能源持仓贡献 -4.1pct，医药贡献 -2.3pct，为主要回撤来源。',
    '定投统计': '【演示】今年定投 10 期，累计投入 20,000 元，当前市值 21,860 元，定投收益 +9.3%。',
    '费率支出': '【演示】今年累计支出管理费/申赎费约 486 元，占资产 0.04%。',
    '现金比例': '【演示】当前现金 163,750 元，占总资产 13.1%，低于目标 15%。',
    '行业分布': '【演示】行业占比前三：白酒 18.5%、银行 12.2%、医药 9.8%，均未超过 20% 集中度上限。',
    '近一月涨跌': '【演示】近一月组合 +2.35%，沪深300 +1.12%，超额 +1.23pct。',
    '分红统计': '【演示】今年累计收到分红 3 笔，共 1,240 元（已再投入 2 笔）。',
    '估值分位': '【演示】市场温度 42（偏低）；最低估指数为恒生（PE 18%/PB 15%）。详见估值仪表盘。',
  }
  for (const chip of ASK_QUICK_QUESTIONS) {
    if (question.includes(chip)) return answers[chip]
  }
  return `【演示】已收到问题「${question}」。接入后端后将走 POST api/v1/ask，命中模板走确定性账本查询返回。`
}

/** 演示公告（时间倒序，含一则清盘预警）。 */
export function mockAnnouncements(): AnnouncementList {
  return {
    announcements: [
      { fund_code: '005911', fund_name: '广发双擎升级混合', title: '广发双擎升级混合：规模连续两季度低于 5000 万元，可能触发清盘', kind: 'liquidation_risk', published_at: '2026-10-20T09:00:00+08:00', summary: '截至 2026Q3 末规模 0.42 亿元，连续两季度低于 5000 万元，敬请关注清盘风险。' },
      { fund_code: '110022', fund_name: '易方达消费行业股票', title: '易方达消费行业股票 2026 年第三次分红公告', kind: 'dividend', published_at: '2026-10-18T15:30:00+08:00', summary: '每 10 份派现 0.85 元，权益登记日 2026-10-22。' },
      { fund_code: '005911', fund_name: '广发双擎升级混合', title: '广发双擎升级混合基金经理变更公告', kind: 'manager_change', published_at: '2026-08-15T09:00:00+08:00', summary: '原基金经理离任，新任基金经理此前管理规模 12 亿元。' },
      { fund_code: '270002', fund_name: '广发稳健增长混合', title: '广发稳健增长混合 2026 年中期分红公告', kind: 'dividend', published_at: '2026-07-10T15:00:00+08:00', summary: '每 10 份派现 0.50 元。' },
      { fund_code: '110022', fund_name: '易方达消费行业股票', title: '易方达消费行业股票招募说明书更新', kind: 'other', published_at: '2026-06-28T09:00:00+08:00', summary: '更新基金经理简介与风险揭示章节。' },
    ],
  }
}

/** 10000 行账本 mock 数据（虚拟滚动性能验证用，确定性生成）。 */
export function mockLedgerRows(count: number): (string | number)[][] {
  const names = ['易方达消费行业股票', '广发双擎升级混合', '招商银行', '沪深300ETF', '广发稳健增长混合', '贵州茅台']
  const codes = ['110022', '005911', '600036', '510300', '270002', '600519']
  const directions = ['买入', '卖出', '分红', '定投']
  const rows: (string | number)[][] = []
  for (let i = 0; i < count; i++) {
    const pick = i % names.length
    const direction = directions[i % directions.length]
    const price = 1 + ((i * 7919) % 10000) / 1000
    const quantity = 100 + ((i * 104729) % 900)
    const amount = price * quantity
    const day = String(1 + (i % 28)).padStart(2, '0')
    const month = String(1 + ((i >> 4) % 12)).padStart(2, '0')
    rows.push([
      names[pick], `2026-${month}-${day} 15:00`, codes[pick], direction,
      amount.toFixed(2), direction === '分红' ? '—' : price.toFixed(4),
      direction === '分红' ? '—' : quantity.toFixed(0), (amount * 0.0003).toFixed(2),
      direction === '买入' ? '—' : ((i % 7 - 3) * 123.4).toFixed(2),
      i % 11 === 0 ? '定投扣款' : '',
    ])
  }
  return rows
}
