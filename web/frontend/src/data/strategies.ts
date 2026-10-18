export const strategyTemplates = [
  { id: 'value', name: '低估值反转', description: '估值分位 + 盈利改善 + 资金流', returns: [3.12, 8.65, 18.42], drawdowns: [-2.14, -4.28, -7.24], sharpe: 1.82, trades: 18 },
  { id: 'trend', name: '趋势跟随', description: '均线突破 + 动量 + 成交量确认', returns: [4.86, 12.35, 23.17], drawdowns: [-3.82, -7.15, -11.08], sharpe: 1.64, trades: 26 },
  { id: 'dividend', name: '高股息防御', description: '股息率 + 低波动 + 财务质量', returns: [1.18, 4.26, 9.62], drawdowns: [-1.24, -2.68, -4.18], sharpe: 1.58, trades: 8 }
]
export const strategyPeriods = ['近1月', '近3月', '近1年']
export function demoStrategyResult(templateId: string, period: string, feeBps: number) {
  const template = strategyTemplates.find(item => item.id === templateId)
  const index = strategyPeriods.indexOf(period)
  if (!template || index < 0 || !Number.isFinite(feeBps) || feeBps < 0 || feeBps > 100) throw new Error('请选择有效的模板、区间和费用。')
  const tradeCount = Math.max(1, Math.round(template.trades * [1 / 12, 1 / 4, 1][index]))
  return { ret: +(template.returns[index] - tradeCount * feeBps / 100).toFixed(2), dd: template.drawdowns[index], sharpe: template.sharpe, tradeCount }
}
export type StrategyResult = ReturnType<typeof demoStrategyResult>
export type Strategy = { id: string; name: string; template: string; period: string; feeBps: number; status: '运行中' | '暂停'; result: StrategyResult | null }
export type StrategyLog = { id: string; name: string; date: string; period: string; feeBps: number; result: StrategyResult }
export const initialStrategies: Strategy[] = strategyTemplates.map((template, index) => ({ id: template.id, name: template.name, template: template.id, period: '近1年', feeBps: 3, status: index === 2 ? '暂停' : '运行中', result: demoStrategyResult(template.id, '近1年', 3) }))
