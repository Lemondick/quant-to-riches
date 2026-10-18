export type Holding = { code: string; name: string; type: '股票' | '基金'; quantity: number; price: number; cost: number; previousClose: number }
export type Transaction = { id: string; date: string; name: string; code: string; type: '股票' | '基金'; direction: '买入' | '卖出' | '分红'; quantity: number; price: number; fee: number; amount: number; realized: number; note: string }
export type Portfolio = { cash: number; holdings: Holding[]; transactions: Transaction[] }
export type TradeInput = Omit<Transaction, 'id' | 'amount' | 'realized'>

export const initialPortfolio: Portfolio = {
  cash: 4133,
  holdings: [
    { code: '600519', name: '贵州茅台', type: '股票', quantity: 10, price: 1542, cost: 1450, previousClose: 1514 },
    { code: '300750', name: '宁德时代', type: '股票', quantity: 22, price: 268.4, cost: 250, previousClose: 262.08 },
    { code: '510300', name: '沪深300ETF', type: '基金', quantity: 2000, price: 3.842, cost: 3.65, previousClose: 3.815 },
    { code: '015042', name: '国泰房地产C', type: '基金', quantity: 4200, price: 1.084, cost: 1.02, previousClose: 1.0714 }
  ],
  transactions: [
    { id: 'demo-1', date: '2026-10-16T10:12:00+08:00', name: '国泰房地产C', code: '015042', type: '基金', direction: '买入', quantity: 461.25, price: 1.084, fee: 0, amount: 500, realized: 0, note: '定投样本' },
    { id: 'demo-2', date: '2026-10-15T14:20:00+08:00', name: '沪深300ETF', code: '510300', type: '基金', direction: '卖出', quantity: 526, price: 4.712, fee: 1.24, amount: 2478.512, realized: 148.72, note: '调仓样本' },
    { id: 'demo-3', date: '2026-10-14T09:41:00+08:00', name: '现金分红', code: '510300', type: '基金', direction: '分红', quantity: 0, price: 86.4, fee: 0, amount: 86.4, realized: 86.4, note: '分红样本' }
  ]
}

export function portfolioTotals(portfolio: Portfolio) {
  const invested = portfolio.holdings.reduce((sum, holding) => sum + holding.price * holding.quantity, 0)
  const cost = portfolio.holdings.reduce((sum, holding) => sum + holding.cost * holding.quantity, 0)
  const daily = portfolio.holdings.reduce((sum, holding) => sum + (holding.price - holding.previousClose) * holding.quantity, 0)
  return { invested, cost, daily, profit: invested - cost, total: invested + portfolio.cash, returnRate: cost ? (invested - cost) / cost * 100 : 0 }
}

export function recordTrade(portfolio: Portfolio, input: TradeInput, id: string): Portfolio {
  if (!/^\d{6}$/.test(input.code) || !input.name.trim()) throw new Error('请输入名称和六位代码。')
  if (!['买入', '卖出', '分红'].includes(input.direction) || !['股票', '基金'].includes(input.type)) throw new Error('请选择有效的交易类型。')
  if (!Number.isFinite(input.price) || input.price <= 0 || !Number.isFinite(input.fee) || input.fee < 0) throw new Error('价格必须大于零，手续费不能为负。')
  if (input.direction !== '分红' && (!Number.isFinite(input.quantity) || input.quantity <= 0)) throw new Error('数量必须大于零。')
  if (Number.isNaN(Date.parse(input.date))) throw new Error('请选择有效的交易时间。')
  const next = structuredClone(portfolio)
  const holding = next.holdings.find(item => item.code === input.code)
  if (holding && holding.type !== input.type) throw new Error('标的类型与已有持仓不一致。')
  const amount = input.direction === '分红' ? input.price : input.price * input.quantity
  let realized = 0
  if (input.direction === '买入') {
    if (amount + input.fee > next.cash + 1e-8) throw new Error('演示账户现金不足，请调整成交金额。')
    next.cash -= amount + input.fee
    if (holding) {
      holding.cost = (holding.cost * holding.quantity + amount + input.fee) / (holding.quantity + input.quantity)
      holding.quantity += input.quantity
    } else next.holdings.push({ code: input.code, name: input.name.trim(), type: input.type, quantity: input.quantity, price: input.price, cost: (amount + input.fee) / input.quantity, previousClose: input.price })
  } else if (input.direction === '卖出') {
    if (!holding || input.quantity > holding.quantity + 1e-8) throw new Error('卖出数量超过可用持仓。')
    if (input.fee > amount) throw new Error('手续费不能超过成交金额。')
    realized = (input.price - holding.cost) * input.quantity - input.fee
    holding.quantity -= input.quantity
    next.holdings = next.holdings.filter(item => item.quantity > 1e-8)
    next.cash += amount - input.fee
  } else {
    if (input.fee > amount) throw new Error('手续费不能超过分红金额。')
    realized = amount - input.fee
    next.cash += realized
  }
  next.cash = Math.round(next.cash * 10000) / 10000
  next.transactions.unshift({ ...input, id, name: input.name.trim(), amount, realized })
  return next
}
