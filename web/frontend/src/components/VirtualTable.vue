<template>
  <div class="virtual-table" role="table" :aria-label="label" :aria-rowcount="rows.length">
    <div class="virtual-thead" role="rowgroup">
      <div class="virtual-tr virtual-head-row" role="row" :style="{ gridTemplateColumns: columns }">
        <div v-for="(header, index) in headers" :key="index" class="virtual-th" role="columnheader"
          :class="{ 'numeric-cell': visibleNumeric.includes(index) }">
          <button v-if="sortable" class="table-sort-button" @click="toggleSort(index)">{{ header }}
            <ArrowUp v-if="sort?.column === index && sort.direction === 'asc'" :size="13" aria-hidden="true" />
            <ArrowDown v-else-if="sort?.column === index" :size="13" aria-hidden="true" />
            <ArrowUpDown v-else :size="12" class="sort-indicator" aria-hidden="true" />
          </button>
          <template v-else>{{ header }}</template>
        </div>
      </div>
    </div>
    <div v-if="rows.length" v-virtual-scroll="scrollBinding" class="virtual-tbody" role="rowgroup" :style="{ maxHeight: height + 'px' }"></div>
    <div v-else class="empty table-empty" role="status"><b>{{ emptyTitle }}</b><span>{{ emptyText }}</span></div>
    <div class="virtual-foot" role="status">虚拟滚动渲染中 <b>{{ renderedRows }}</b> 行（共 {{ rows.length }} 行）</div>
  </div>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-vue-next'
import { escapeHtml, sortTableRows, type TableCell, type TableSort } from '../utils/table'
import type { VirtualScrollBinding } from '../directives/virtualScroll'

const props = withDefaults(defineProps<{
  headers: string[]
  rows: TableCell[][]
  label?: string
  numericColumns?: number[]
  sortable?: boolean
  rowHeight?: number
  height?: number
  columnWidths?: string
  /** 可见列位置 → 原始列索引（配合 DataTable 列隐藏），默认恒等映射。 */
  columnIndexes?: number[]
  /** 自定义单元格 HTML（返回 null 时走默认转义文本）；用于虚拟模式下保留标签等样式。 */
  cellHtml?: (value: TableCell, columnIndex: number, row: TableCell[]) => string | null
  emptyTitle?: string
  emptyText?: string
}>(), {
  label: '数据表格', numericColumns: () => [], sortable: true, rowHeight: 36, height: 420,
  emptyTitle: '暂无匹配记录', emptyText: '请尝试调整筛选条件或搜索关键词。',
})

const sort = ref<TableSort | null>(null)
const renderedRows = ref(0)
const indexes = computed(() => props.columnIndexes ?? props.headers.map((_, i) => i))
const sortedRows = computed(() => {
  const source = !props.sortable || !sort.value
    ? props.rows.map((row, index) => ({ row, index }))
    : sortTableRows(props.rows, { column: indexes.value[sort.value.column] ?? sort.value.column, direction: sort.value.direction })
  return source.map(entry => entry.row)
})
const visibleNumeric = computed(() => props.numericColumns
  .map(original => indexes.value.indexOf(original)).filter(i => i >= 0))
const columns = computed(() => props.columnWidths ?? `repeat(${props.headers.length}, minmax(0, 1fr))`)

function toggleSort(column: number) {
  const current = sort.value
  sort.value = { column, direction: current?.column === column && current.direction === 'asc' ? 'desc' : 'asc' }
}

function cellClass(value: TableCell, index: number): string {
  const classes = ['virtual-td']
  if (visibleNumeric.value.includes(index)) classes.push('numeric-cell')
  if (typeof value === 'string' && /^[+-]/.test(value.trim())) classes.push(value.trim().startsWith('-') ? 'down' : 'up')
  return classes.join(' ')
}

function renderRow(row: TableCell[], index: number): string {
  const cells = indexes.value.map((originalIndex, columnIndex) => {
    const value = row[originalIndex]
    const custom = props.cellHtml?.(value, originalIndex, row)
    const content = custom ?? escapeHtml(value === null || value === undefined ? '' : String(value))
    return `<div class="${cellClass(value, columnIndex)}" role="cell">${content}</div>`
  }).join('')
  return `<div class="virtual-tr" role="row" aria-rowindex="${index + 2}" style="display:grid;grid-template-columns:${columns.value};height:100%">${cells}</div>`
}

const scrollBinding = computed<VirtualScrollBinding<TableCell[]>>(() => ({
  items: sortedRows.value,
  rowHeight: props.rowHeight,
  overscan: 8,
  renderRow,
  rowClass: 'virtual-row',
  onRange: (range) => { renderedRows.value = range.end - range.start },
}))
</script>
<style scoped>
.virtual-table{border:1px solid var(--border);border-radius:10px;overflow:hidden;background:rgba(13,24,40,.55)}
.virtual-thead{position:sticky;top:0;z-index:2}
.virtual-head-row{background:rgba(22,38,62,.95);border-bottom:1px solid var(--border)}
.virtual-tr{display:grid;align-items:stretch}
.virtual-th{padding:10px 12px;font-size:11px;color:var(--text-2);text-align:left;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.virtual-th.numeric-cell,.virtual-td.numeric-cell{text-align:right;font-variant-numeric:tabular-nums}
.virtual-tbody{overflow-y:auto;background:transparent}
.virtual-tbody :deep(.vscroll-row){border-bottom:1px solid rgba(34,51,77,.45)}
.virtual-tbody :deep(.virtual-row .virtual-td){padding:0 12px;display:flex;align-items:center;font-size:12px;color:var(--text-1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.virtual-foot{padding:8px 12px;font-size:11px;color:var(--text-2);border-top:1px solid var(--border);background:rgba(22,38,62,.6)}
.table-empty{padding:28px 12px;text-align:center;display:flex;flex-direction:column;gap:6px;align-items:center}
</style>
