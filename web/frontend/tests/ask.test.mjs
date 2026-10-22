import test from 'node:test'
import assert from 'node:assert/strict'
import { ASK_QUICK_QUESTIONS } from '../src/services/batch2Api.ts'
import { mockAskAnswer } from '../src/data/batch2.ts'

test('exactly 10 quick questions are defined', () => {
  assert.equal(ASK_QUICK_QUESTIONS.length, 10)
  for (const expected of [
    '今年收益',
    '盈亏排名',
    '回撤贡献',
    '定投统计',
    '费率支出',
    '现金比例',
    '行业分布',
    '近一月涨跌',
    '分红统计',
    '估值分位',
  ]) {
    assert.ok(ASK_QUICK_QUESTIONS.includes(expected), `missing chip: ${expected}`)
  }
})

test('mock answers cover all 10 quick questions and are marked as demo', () => {
  for (const chip of ASK_QUICK_QUESTIONS) {
    const answer = mockAskAnswer(chip)
    assert.ok(answer.startsWith(''), `answer for ${chip} should be marked as demo`)
    assert.ok(answer.length > 10)
  }
})

test('unknown questions get a generic demo reply', () => {
  const answer = mockAskAnswer('明天会涨吗')
  assert.ok(answer.includes('POST api/v1/ask'))
})
