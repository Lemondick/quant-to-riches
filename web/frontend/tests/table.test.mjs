import test from 'node:test'
import assert from 'node:assert/strict'
import { readTablePreferences, sortTableRows } from '../src/utils/table.ts'

test('currency and Chinese amount units sort numerically', () => {
  const rows = [
    ['A', '¥ 3,824亿'],
    ['B', '¥ 18.4亿'],
    ['C', '¥ 2,168亿'],
    ['D', '980万'],
  ]
  assert.deepEqual(
    sortTableRows(rows, { column: 1, direction: 'asc' }).map((entry) => entry.row[0]),
    ['D', 'B', 'C', 'A'],
  )
  assert.equal(rows[0][0], 'A')
})

test('negative percentages sort correctly and unavailable values remain last', () => {
  const rows = [
    ['A', '+0.71%'],
    ['B', '—'],
    ['C', '-0.34%'],
    ['D', '+2.41%'],
  ]
  assert.deepEqual(
    sortTableRows(rows, { column: 1, direction: 'asc' }).map((entry) => entry.row[0]),
    ['C', 'A', 'D', 'B'],
  )
  assert.deepEqual(
    sortTableRows(rows, { column: 1, direction: 'desc' }).map((entry) => entry.row[0]),
    ['D', 'A', 'C', 'B'],
  )
})

test('sorting preserves original row identities for custom cells', () => {
  const rows = [
    ['A', 3],
    ['B', 1],
    ['C', 1],
  ]
  assert.deepEqual(
    sortTableRows(rows, { column: 1, direction: 'asc' }).map((entry) => entry.index),
    [1, 2, 0],
  )
  assert.deepEqual(
    sortTableRows(rows, null).map((entry) => entry.index),
    [0, 1, 2],
  )
})

test('invalid stored preferences recover to a usable default', () => {
  const defaults = { density: 'comfortable', hiddenColumns: [], sort: null }
  for (const raw of [null, 'broken json', 'null', '12']) assert.deepEqual(readTablePreferences(raw, 8), defaults)
})

test('stored column choices cannot hide locked columns or reference missing columns', () => {
  const saved = JSON.stringify({
    density: 'compact',
    hiddenColumns: [0, 2, 2, 4, 90, -1, '3'],
    sort: { column: 3, direction: 'desc' },
  })
  assert.deepEqual(readTablePreferences(saved, 8, [0, 4]), {
    density: 'compact',
    hiddenColumns: [2],
    sort: { column: 3, direction: 'desc' },
  })
  assert.equal(readTablePreferences('{"sort":{"column":99,"direction":"asc"}}', 8).sort, null)
})
