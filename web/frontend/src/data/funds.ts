export const fundPeriods = [
  { label: '日涨跌', key: 'dayChange' },
  { label: '近1周', key: 'w1' },
  { label: '近1月', key: 'm1' },
  { label: '近3月', key: 'm3' },
  { label: '今年以来', key: 'ytd' },
  { label: '近1年', key: 'y1' }
] as const

export type FundPeriod = typeof fundPeriods[number]['key']

// Illustrative UI fixtures only. These values are not live fund performance or ratings.
export const fundList = [
  { code: '512000', name: '华宝中证全指证券ETF', tag: '券商爆款', type: '指数型/ETF', nav: '1.0820', dayChange: '+3.42%', w1: '+5.12%', m1: '+8.65%', m3: '+15.20%', ytd: '+19.45%', y1: '+21.60%', manager: '丰晨成', size: '245.8亿', stars: 5 },
  { code: '320007', name: '诺安成长混合', tag: '芯片先锋', type: '混合型', nav: '1.6240', dayChange: '+3.18%', w1: '+4.38%', m1: '+16.85%', m3: '+22.40%', ytd: '+28.12%', y1: '+25.32%', manager: '刘慧影', size: '186.2亿', stars: 4 },
  { code: '159995', name: '华夏国证半导体芯片ETF', tag: '硬科技', type: '指数型/ETF', nav: '1.1560', dayChange: '+2.95%', w1: '+6.24%', m1: '+14.20%', m3: '+19.85%', ytd: '+24.60%', y1: '+32.48%', manager: '赵宗原', size: '218.4亿', stars: 5 },
  { code: '161725', name: '招商中证白酒指数(LOF)A', tag: '消费核心', type: '指数型/ETF', nav: '0.9840', dayChange: '+2.45%', w1: '+2.18%', m1: '+6.82%', m3: '+11.45%', ytd: '+13.80%', y1: '-2.36%', manager: '侯昊', size: '412.5亿', stars: 5 },
  { code: '000978', name: '景顺长城量化新动力股票', tag: '量化优选', type: '股票型', nav: '1.5420', dayChange: '+1.68%', w1: '+3.45%', m1: '+7.92%', m3: '+13.15%', ytd: '+17.20%', y1: '+18.65%', manager: '黎海威', size: '65.4亿', stars: 5 },
  { code: '005827', name: '易方达蓝筹精选混合', tag: '价值白马', type: '混合型', nav: '1.8620', dayChange: '+1.15%', w1: '+1.76%', m1: '+5.60%', m3: '+9.80%', ytd: '+12.35%', y1: '+9.72%', manager: '张坤', size: '420.1亿', stars: 5 },
  { code: '510300', name: '华泰柏瑞沪深300ETF', tag: '旗舰宽基', type: '指数型/ETF', nav: '3.8420', dayChange: '+0.71%', w1: '+1.25%', m1: '+4.18%', m3: '+8.52%', ytd: '+14.65%', y1: '+16.84%', manager: '柳军', size: '1,245.0亿', stars: 5 },
  { code: '001594', name: '天弘中证红利低波动100A', tag: '高股息', type: '指数型/ETF', nav: '1.4180', dayChange: '+0.52%', w1: '+0.86%', m1: '+3.10%', m3: '+6.80%', ytd: '+15.92%', y1: '+17.40%', manager: '刘笑明', size: '82.3亿', stars: 5 },
  { code: '040046', name: '华安纳斯达克100ETF联接', tag: '全球科技', type: 'QDII/海外', nav: '3.2840', dayChange: '+0.86%', w1: '-0.42%', m1: '+5.12%', m3: '+10.45%', ytd: '+22.18%', y1: '+29.56%', manager: '倪斌', size: '98.7亿', stars: 5 },
  { code: '003096', name: '中欧医疗健康混合A', tag: '医药白马', type: '混合型', nav: '1.7742', dayChange: '-0.32%', w1: '-1.08%', m1: '+4.24%', m3: '+7.15%', ytd: '+8.90%', y1: '-5.24%', manager: '葛兰', size: '328.6亿', stars: 4 },
  { code: '000171', name: '易方达裕丰回报债券', tag: '固收稳健', type: '债券型', nav: '1.3920', dayChange: '+0.08%', w1: '+0.16%', m1: '+1.02%', m3: '+2.45%', ytd: '+4.85%', y1: '+5.18%', manager: '张清华', size: '185.3亿', stars: 5 }
]

export type Fund = typeof fundList[number]

export function rankFunds(funds: Fund[], filters: { period: FundPeriod; category: string; query: string; ascending: boolean }) {
  const query = filters.query.trim().toLowerCase()
  return funds.filter(fund =>
    (filters.category === '全部' || fund.type === filters.category) &&
    (!query || [fund.name, fund.code, fund.manager].some(value => value.toLowerCase().includes(query)))
  ).sort((a, b) => {
    const difference = parseFloat(a[filters.period]) - parseFloat(b[filters.period])
    return (filters.ascending ? difference : -difference) || a.code.localeCompare(b.code)
  })
}

export function summarizeFunds(funds: Fund[], period: FundPeriod) {
  const ranked = [...funds].sort((a, b) => parseFloat(b[period]) - parseFloat(a[period]))
  const values = ranked.map(fund => parseFloat(fund[period]))
  return {
    top: ranked[0] ?? null,
    last: ranked[ranked.length - 1] ?? null,
    count: ranked.length,
    positiveCount: values.filter(value => value > 0).length,
    average: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
  }
}

export function formatReturn(value: number | null) {
  if (value === null) return '—'
  return (value > 0 ? '+' : '') + value.toFixed(2) + '%'
}
