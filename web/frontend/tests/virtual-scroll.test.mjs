import test from 'node:test'
import assert from 'node:assert/strict'
import { computeVisibleRange, renderedRowCount, totalContentHeight } from '../src/utils/virtualScroll.ts'

const ROWS = 10000
const ROW_HEIGHT = 36
const VIEWPORT = 420

test('10000 rows: visible window renders only a small slice', () => {
  const range = computeVisibleRange(0, VIEWPORT, ROW_HEIGHT, ROWS)
  const count = renderedRowCount(range)
  // 420/36 ≈ 11.7 行 + 上下 overscan 各 5
  assert.ok(count <= 25, `expected <= 25 rendered rows, got ${count}`)
  assert.ok(count >= 12, `expected >= 12 rendered rows, got ${count}`)
  assert.equal(range.start, 0)
})

test('10000 rows: mid-scroll window is correctly positioned', () => {
  const scrollTop = 180000 // 第 5000 行附近
  const range = computeVisibleRange(scrollTop, VIEWPORT, ROW_HEIGHT, ROWS)
  assert.ok(range.start <= 5000 && range.end >= 5000, `window ${range.start}-${range.end} should cover row 5000`)
  assert.equal(renderedRowCount(range), Math.ceil(VIEWPORT / ROW_HEIGHT) + 10)
})

test('10000 rows: bottom window clamps to item count', () => {
  const range = computeVisibleRange(totalContentHeight(ROWS, ROW_HEIGHT), VIEWPORT, ROW_HEIGHT, ROWS)
  assert.equal(range.end, ROWS)
  assert.ok(range.start < ROWS)
})

test('overscan adds buffer rows above and below', () => {
  const scrollTop = 3600 // 第 100 行
  const plain = computeVisibleRange(scrollTop, VIEWPORT, ROW_HEIGHT, ROWS, 0)
  const buffered = computeVisibleRange(scrollTop, VIEWPORT, ROW_HEIGHT, ROWS, 5)
  assert.equal(plain.start, 100)
  assert.equal(buffered.start, 95)
  assert.equal(buffered.end - buffered.start, plain.end - plain.start + 10)
})

test('edge cases never produce negative or out-of-range windows', () => {
  assert.deepEqual(computeVisibleRange(0, VIEWPORT, 0, ROWS), { start: 0, end: 0 })
  assert.deepEqual(computeVisibleRange(0, VIEWPORT, ROW_HEIGHT, 0), { start: 0, end: 0 })
  assert.deepEqual(
    computeVisibleRange(-50, VIEWPORT, ROW_HEIGHT, ROWS),
    computeVisibleRange(0, VIEWPORT, ROW_HEIGHT, ROWS),
  )
  assert.deepEqual(computeVisibleRange(99999999, VIEWPORT, ROW_HEIGHT, ROWS).end, ROWS)
  assert.equal(totalContentHeight(ROWS, ROW_HEIGHT), 360000)
})
