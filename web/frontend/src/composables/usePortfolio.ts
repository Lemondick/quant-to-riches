import { computed } from 'vue'
import { useLocalState } from './useLocalState'
import { initialPortfolio, portfolioTotals, recordTrade, type Portfolio, type Holding, type TradeInput } from '../utils/portfolio'

const portfolio = useLocalState<Portfolio>('guanlan.portfolio.v1', initialPortfolio, (value): value is Portfolio => {
  if (!value || typeof value !== 'object') return false
  const data = value as Portfolio
  return Number.isFinite(data.cash) && data.cash >= 0 && Array.isArray(data.holdings) && data.holdings.every(item => /^\d{6}$/.test(item.code) && typeof item.name === 'string' && ['股票', '基金'].includes(item.type) && [item.quantity, item.price, item.cost, item.previousClose].every(number => Number.isFinite(number) && number > 0)) && Array.isArray(data.transactions) && data.transactions.every(item => typeof item.id === 'string' && typeof item.date === 'string' && typeof item.name === 'string' && ['买入', '卖出', '分红'].includes(item.direction) && [item.price, item.quantity, item.amount, item.fee, item.realized].every(Number.isFinite))
})
const totals = computed(() => portfolioTotals(portfolio.value))

export function usePortfolio() {
  function saveHolding(holding: Holding, previousCode?: string) {
    if (!/^\d{6}$/.test(holding.code) || !holding.name.trim()) throw new Error('请输入持仓名称和六位代码。')
    if ([holding.quantity, holding.price, holding.cost, holding.previousClose].some(value => !Number.isFinite(value) || value <= 0)) throw new Error('数量、现价、成本与昨收必须大于零。')
    if (portfolio.value.holdings.some(item => item.code === holding.code && item.code !== previousCode)) throw new Error('该代码已存在，请编辑已有持仓。')
    const index = portfolio.value.holdings.findIndex(item => item.code === previousCode)
    const copy = { ...holding, name: holding.name.trim() }
    if (index >= 0) portfolio.value.holdings[index] = copy
    else portfolio.value.holdings.push(copy)
  }
  function addTrade(input: TradeInput) { portfolio.value = recordTrade(JSON.parse(JSON.stringify(portfolio.value)), input, crypto.randomUUID()) }
  return { portfolio, totals, saveHolding, addTrade }
}
