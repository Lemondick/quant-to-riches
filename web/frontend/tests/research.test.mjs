import test from 'node:test'
import assert from 'node:assert/strict'
import { inferCriteria, screenStocks } from '../src/data/research.ts'
import { demoStrategyResult } from '../src/data/strategies.ts'
import { makeDemoChart } from '../src/utils/charts.ts'
import { csvText, setDisplayTimezone, dateTime } from '../src/utils/format.ts'

test('recognized research conditions remain explicit and unknown requests yield no inferred rule', () => {
  assert.deepEqual(inferCriteria('低估值、高股息、趋势改善和盈利改善'), ['value', 'dividend', 'momentum', 'quality'])
  assert.deepEqual(inferCriteria('一个未支持的主题'), [])
  assert.deepEqual(screenStocks([]), [])
  assert.ok(screenStocks(['value', 'quality']).every((stock) => stock.pe <= 20 && stock.growth > 0))
  assert.ok(!screenStocks(['quality']).some((stock) => stock.code === '600048'))
})

test('sample strategy periods and fee assumptions produce distinct labeled results', () => {
  const month = demoStrategyResult('value', '近1月', 3)
  const year = demoStrategyResult('value', '近1年', 3)
  assert.notEqual(month.ret, year.ret)
  assert.ok(demoStrategyResult('value', '近1年', 10).ret < demoStrategyResult('value', '近1年', 0).ret)
  assert.throws(() => demoStrategyResult('unknown', '近1年', 3))
  assert.throws(() => demoStrategyResult('value', '近1周', 3))
  assert.throws(() => demoStrategyResult('value', '近1年', -1))
})

test('chart series, colors and end values agree with every supplied legend', () => {
  const legends = [
    { name: 'A', value: '+5.31%', color: '#4d8dff' },
    { name: 'B', value: '+3.17%', color: '#ff9d54' },
    { name: 'C', value: '-2.5%', color: '#42ddb1' },
  ]
  const chart = makeDemoChart(legends, '近3月')
  assert.equal(chart.series.length, 3)
  for (const [index, series] of chart.series.entries()) {
    assert.equal(series.color, legends[index].color)
    assert.equal(series.values.length, chart.labels.length)
    assert.equal(series.values[0], 0)
    assert.equal(series.values.at(-1), parseFloat(legends[index].value))
    assert.ok(series.values.every(Number.isFinite))
  }
  assert.notDeepEqual(makeDemoChart(legends, '近1月').series[0].values, chart.series[0].values)
  assert.ok(chart.labels.every((label) => /^\d{2}-\d{2}$/.test(label)))
  const intraday = makeDemoChart(legends, '分时')
  assert.equal(intraday.labels[0], '09:30')
  assert.equal(intraday.labels.at(-1), '15:00')
})

test('CSV escapes quotes, newlines and formula-like text while retaining numeric losses', () => {
  const output = csvText(
    ['名称', '收益'],
    [
      ['带"引号\n名称', '-2.3%'],
      ['=SUM(A1)', '+3.2%'],
    ],
  )
  assert.ok(output.startsWith('\uFEFF'))
  assert.ok(output.includes('带""引号\n名称'))
  assert.ok(output.includes("'=SUM(A1)"))
  assert.ok(output.includes('"-2.3%"'))
})

test('saved display timezone changes shown timestamps', () => {
  setDisplayTimezone('Asia/Shanghai')
  assert.ok(dateTime('2026-10-16T00:00:00Z').includes('08:00'))
  setDisplayTimezone('Asia/Tokyo')
  assert.ok(dateTime('2026-10-16T00:00:00Z').includes('09:00'))
  setDisplayTimezone('Asia/Shanghai')
})
