import test from 'node:test'
import assert from 'node:assert/strict'
import { cheapestIndex, percentileTone, temperatureLabel } from '../src/services/batch2Api.ts'
import { mockValuation } from '../src/data/batch2.ts'

test('temperature label falls back to score bands', () => {
  assert.equal(temperatureLabel(10), '低估')
  assert.equal(temperatureLabel(42), '偏低')
  assert.equal(temperatureLabel(50), '合理')
  assert.equal(temperatureLabel(65), '偏高')
  assert.equal(temperatureLabel(90), '高估')
})

test('percentile tone: low is cheap (green), high is expensive (red)', () => {
  assert.equal(percentileTone(18), 'low')
  assert.equal(percentileTone(52), 'mid')
  assert.equal(percentileTone(90), 'high')
})

test('cheapest index picks the lowest average percentile', () => {
  const cheapest = cheapestIndex(mockValuation())
  assert.equal(cheapest?.symbol, 'HSI') // PE 18% / PB 15%，六指数中最低
})

test('cheapest index returns null without items', () => {
  assert.equal(cheapestIndex({ as_of: '2026-10-21', items: [], temperature: { score: 50, label: '' } }), null)
})
