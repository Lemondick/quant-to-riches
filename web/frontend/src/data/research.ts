import { fundList } from './funds.ts'

export const reviewCatalog = [...fundList, { code: '015042', name: '国泰国证房地产行业指数C', tag: '行业样本', type: '指数型/ETF', nav: '1.0840', dayChange: '+1.18%', w1: '+3.62%', m1: '+6.81%', m3: '+8.24%', ytd: '+10.42%', y1: '-3.15%', manager: '演示经理', size: '42.6亿', stars: 3 }]

export const researchCriteria = [
  { id: 'value', label: '低估值', description: '市盈率不高于 20 倍' },
  { id: 'dividend', label: '高股息', description: '样本股息率不低于 3%' },
  { id: 'momentum', label: '趋势改善', description: '近90日涨幅大于 0' },
  { id: 'quality', label: '盈利改善', description: '样本利润增速大于 0' }
] as const
export type ResearchCriterion = typeof researchCriteria[number]['id']
export const stockSamples = [
  { code: '600519', name: '贵州茅台', industry: '食品饮料', pe: 19.4, dividend: 3.2, momentum: 8.6, growth: 12.4 },
  { code: '600036', name: '招商银行', industry: '银行', pe: 7.2, dividend: 5.1, momentum: 5.3, growth: 2.8 },
  { code: '601318', name: '中国平安', industry: '保险', pe: 8.6, dividend: 4.8, momentum: 6.8, growth: 7.6 },
  { code: '300750', name: '宁德时代', industry: '新能源', pe: 22.5, dividend: 1.8, momentum: 18.3, growth: 15.2 },
  { code: '688981', name: '中芯国际', industry: '半导体', pe: 68.2, dividend: 0.2, momentum: 24.6, growth: -3.2 },
  { code: '600048', name: '保利发展', industry: '房地产', pe: 12.8, dividend: 3.6, momentum: 4.2, growth: -8.4 }
]
export function inferCriteria(query: string): ResearchCriterion[] {
  const result: ResearchCriterion[] = []
  if (/低估|估值|价值/.test(query)) result.push('value')
  if (/股息|红利|分红/.test(query)) result.push('dividend')
  if (/趋势|突破|成长|动量/.test(query)) result.push('momentum')
  if (/盈利|利润|质量|现金流/.test(query)) result.push('quality')
  return result
}
export function screenStocks(criteria: ResearchCriterion[]) {
  if (!criteria.length) return []
  return stockSamples.filter(stock => criteria.every(criterion => criterion === 'value' ? stock.pe <= 20 : criterion === 'dividend' ? stock.dividend >= 3 : criterion === 'momentum' ? stock.momentum > 0 : stock.growth > 0))
}

// Fictional article fixtures; no live news or external sourcing is implied.
export const newsSamples = [
  { id: 'policy', type: '政策', time: '10-16 10:31', tag: '重要', title: '稳增长政策预期升温，地产链条关注度提升', desc: '市场聚焦后续增量政策与行业基本面修复节奏，地产、基建相关板块出现资金活跃迹象。', codes: ['015042', '600048'], body: ['本条为本地演示资讯，用于展示政策信息与研究标的的关联方式。', '研究时可分别记录政策发布日期、执行条件以及关联公司的实际经营数据，再将观察结果写入复核备注。'] },
  { id: 'macro', type: '宏观', time: '10-16 09:58', tag: '宏观', title: '主要指数震荡走强，成交额维持活跃', desc: '风险偏好较前期有所改善，成长与顺周期板块轮动明显，投资者关注量价配合。', codes: ['510300'], body: ['本条为指数行情相关的演示内容，数值与描述不代表真实市场状态。', '可以从指数涨跌、市场广度和成交额三个角度建立自己的观察记录。'] },
  { id: 'industry', type: '行业', time: '10-16 09:32', tag: '行业', title: '房地产行业估值处于历史偏低区域', desc: '行业估值修复仍取决于销售、融资与库存去化，短期交易性机会与政策催化并存。', codes: ['015042'], body: ['本条为行业研究界面的演示样本，不构成对当前行业估值的事实判断。', '查看关联基金后，可以在基金复核页记录估值口径、样本范围和后续需要核实的问题。'] },
  { id: 'company', type: '公司', time: '10-15 15:20', tag: '公司', title: '多家公司披露三季度经营数据', desc: '关注盈利超预期、现金流改善以及机构持仓变化，避免仅依据单一事件做决策。', codes: ['600519', '300750'], body: ['本条为公司事件的演示样本，未连接公告或新闻数据服务。', '正式研究时应回到原始公告核对报告期间、财务口径和同比基数。'] }
]
