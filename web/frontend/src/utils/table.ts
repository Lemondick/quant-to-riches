export type TableCell = string | number
export type TableSort = { column: number; direction: 'asc' | 'desc' } | null
export type TablePreferences = {
  density: 'comfortable' | 'compact'
  hiddenColumns: number[]
  sort: TableSort
}

export function readTablePreferences(raw: string | null, columnCount: number, lockedColumns = [0]): TablePreferences {
  const defaults: TablePreferences = { density: 'comfortable', hiddenColumns: [], sort: null }
  if (!raw) return defaults
  try {
    const value = JSON.parse(raw)
    if (!value || typeof value !== 'object') return defaults
    const validColumn = (column: unknown): column is number =>
      Number.isInteger(column) && Number(column) >= 0 && Number(column) < columnCount
    return {
      density: value.density === 'compact' ? 'compact' : 'comfortable',
      hiddenColumns: Array.isArray(value.hiddenColumns)
        ? [...new Set<number>(value.hiddenColumns.filter((column: unknown) => validColumn(column) && !lockedColumns.includes(column)))]
        : [],
      sort: value.sort && validColumn(value.sort.column) && ['asc', 'desc'].includes(value.sort.direction)
        ? { column: value.sort.column, direction: value.sort.direction }
        : null
    }
  } catch {
    return defaults
  }
}

function numericValue(value: TableCell): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const normalized = value.replace(/[,\s¥￥%]/g, '')
  const match = normalized.match(/^([+-]?\d+(?:\.\d+)?)(万|亿)?$/)
  if (!match) return null
  return Number(match[1]) * (match[2] === '亿' ? 1e8 : match[2] === '万' ? 1e4 : 1)
}

export function sortTableRows(rows: TableCell[][], sort: TableSort) {
  const indexed = rows.map((row, index) => ({ row, index }))
  if (!sort) return indexed
  return indexed.sort((a, b) => {
    const left = a.row[sort.column]
    const right = b.row[sort.column]
    const leftMissing = left == null || left === '' || left === '—'
    const rightMissing = right == null || right === '' || right === '—'
    if (leftMissing || rightMissing) return Number(leftMissing) - Number(rightMissing)
    const leftNumber = numericValue(left)
    const rightNumber = numericValue(right)
    const comparison = leftNumber !== null && rightNumber !== null
      ? leftNumber - rightNumber
      : String(left).localeCompare(String(right), 'zh-CN', { numeric: true })
    return sort.direction === 'asc' ? comparison : -comparison
  })
}
