<template>
  <div class="data-table" :class="{ 'is-compact': preferences.density === 'compact' }">
    <div class="data-table-toolbar">
      <div class="table-record-count" role="status" aria-live="polite" aria-atomic="true">
        <slot name="summary">共 <b>{{ rows.length }}</b> 条记录</slot>
        <button v-if="preferences.sort && sortable" class="text-action" @click="preferences.sort = null">清除排序</button>
      </div>
      <div class="table-display-tools">
        <div class="density-switch" role="group" aria-label="表格密度">
          <button :aria-pressed="preferences.density === 'comfortable'" :class="{ active: preferences.density === 'comfortable' }" @click="preferences.density = 'comfortable'">
            <Rows3 :size="14" aria-hidden="true" />舒适
          </button>
          <button :aria-pressed="preferences.density === 'compact'" :class="{ active: preferences.density === 'compact' }" @click="preferences.density = 'compact'">
            <List :size="14" aria-hidden="true" />紧凑
          </button>
        </div>
        <details ref="columnMenu" class="column-settings" @keydown.esc.prevent="closeColumns">
          <summary><Columns3 :size="14" aria-hidden="true" />显示列<ChevronDown :size="12" aria-hidden="true" /></summary>
          <div class="column-settings-popover">
            <div class="column-settings-title"><b>自定义显示列</b><button class="text-action" @click="preferences.hiddenColumns = []">恢复默认</button></div>
            <p>名称等关键列始终保留</p>
            <label v-for="(header, index) in headers" :key="index" :class="{ 'is-locked': lockedColumns.includes(index) }">
              <input type="checkbox" :checked="!preferences.hiddenColumns.includes(index)" :disabled="lockedColumns.includes(index)" @change="toggleColumn(index)" />
              <span>{{ header }}</span><span v-if="lockedColumns.includes(index)" class="column-required">固定</span>
            </label>
          </div>
        </details>
      </div>
    </div>
    <div class="table-wrap" :tabindex="rows.length ? 0 : undefined" :aria-label="label + '，可横向滚动'">
      <VirtualTable v-if="useVirtual" :headers="visibleColumns.map(c => c.label)" :rows="rows"
        :column-indexes="visibleColumns.map(c => c.index)" :label="label" :numeric-columns="numericColumns"
        :sortable="sortable" :row-height="virtualRowHeight" :empty-title="emptyTitle" :empty-text="emptyText" />
      <table v-else :aria-label="label" :style="{ minWidth: Math.max(240, Math.round(minWidth * visibleColumns.length / headers.length)) + 'px' }">
        <thead>
          <tr>
            <th v-for="column in visibleColumns" :key="column.index" scope="col" :class="columnClasses(column.index)" :aria-sort="sortState(column.index)">
              <button v-if="sortable && column.label !== '操作'" class="table-sort-button" @click="toggleSort(column.index)">
                {{ column.label }}
                <ArrowUp v-if="preferences.sort?.column === column.index && preferences.sort.direction === 'asc'" :size="13" aria-hidden="true" />
                <ArrowDown v-else-if="preferences.sort?.column === column.index" :size="13" aria-hidden="true" />
                <ArrowUpDown v-else :size="12" class="sort-indicator" aria-hidden="true" />
              </button>
              <template v-else>{{ column.label }}</template>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in displayedRows" :key="entry.index">
            <td v-for="column in visibleColumns" :key="column.index" :class="[columnClasses(column.index), cellTone(entry.row[column.index])]">
              <slot :name="'cell-' + column.index" :value="entry.row[column.index]" :row="entry.row" :row-index="entry.index">{{ entry.row[column.index] }}</slot>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="empty table-empty" role="status">
        <FileSearch :size="30" aria-hidden="true" />
        <b>{{ emptyTitle }}</b><span>{{ emptyText }}</span>
        <slot name="empty-action" />
      </div>
    </div>
    <div v-if="pageSize && rows.length > pageSize && !useVirtual" class="data-table-toolbar table-pagination"><button class="btn" :disabled="page === 1" @click="page--">上一页</button><span role="status">第 {{ page }} / {{ pageCount }} 页 · 共 {{ rows.length }} 条</span><button class="btn" :disabled="page === pageCount" @click="page++">下一页</button></div>
    <div class="data-table-foot"><span>{{ preferences.density === 'compact' ? '紧凑视图' : '舒适视图' }} · {{ visibleColumns.length }} / {{ headers.length }} 列</span><span>视图设置自动保存</span></div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, Columns3, FileSearch, List, Rows3 } from 'lucide-vue-next'
import { readTablePreferences, sortTableRows, type TableCell } from '../utils/table'
import { usePreferences } from '../composables/usePreferences'
import VirtualTable from './VirtualTable.vue'

const props = withDefaults(defineProps<{
  headers: string[]
  rows: TableCell[][]
  tableId?: string
  label?: string
  numericColumns?: number[]
  lockedColumns?: number[]
  highlightColumn?: number
  sortable?: boolean
  minWidth?: number
  emptyTitle?: string
  emptyText?: string
  pageSize?: number
  /** 启用虚拟滚动：行数超过 virtualThreshold 时用 v-virtual-scroll 渲染（分页自动停用）。 */
  virtual?: boolean
  virtualThreshold?: number
  virtualRowHeight?: number
}>(), {
  label: '数据表格', numericColumns: () => [], lockedColumns: () => [0], sortable: true,
  minWidth: 640, emptyTitle: '暂无匹配记录', emptyText: '请尝试调整筛选条件或搜索关键词。',
  virtual: false, virtualThreshold: 300, virtualRowHeight: 36,
})

const storageKey = 'guanlan.table.v1.' + (props.tableId ?? props.headers.join('|'))
const { preferences: workspacePreferences } = usePreferences()
function loadPreferences() {
  let raw: string | null = null
  try { raw = localStorage.getItem(storageKey) } catch { /* Storage may be disabled. */ }
  const settings = readTablePreferences(raw, props.headers.length, props.lockedColumns)
  if (!raw && workspacePreferences.value.density === 'compact') settings.density = 'compact'
  return settings
}
const preferences = ref(loadPreferences())
watch(preferences, (value) => {
  try { localStorage.setItem(storageKey, JSON.stringify(value)) } catch { /* Keep the current session usable without storage. */ }
}, { deep: true })

const visibleColumns = computed(() => props.headers
  .map((label, index) => ({ label, index }))
  .filter(column => props.lockedColumns.includes(column.index) || !preferences.value.hiddenColumns.includes(column.index)))
const page = ref(1)
const pageCount = computed(() => props.pageSize ? Math.max(1, Math.ceil(props.rows.length / props.pageSize)) : 1)
watch([() => props.rows.length, () => preferences.value.sort], () => { page.value = 1 })
const displayedRows = computed(() => {
  const sorted = sortTableRows(props.rows, props.sortable && !useVirtual.value ? preferences.value.sort : null)
  return props.pageSize ? sorted.slice((page.value - 1) * props.pageSize, page.value * props.pageSize) : sorted
})
/** 行数超过阈值才启用虚拟滚动，小表保持原表格渲染；虚拟模式下排序由 VirtualTable 内部处理。 */
const useVirtual = computed(() => props.virtual && props.rows.length > (props.virtualThreshold ?? 300))

function toggleColumn(index: number) {
  if (props.lockedColumns.includes(index)) return
  const hidden = preferences.value.hiddenColumns
  preferences.value.hiddenColumns = hidden.includes(index) ? hidden.filter(column => column !== index) : [...hidden, index]
}
function toggleSort(column: number) {
  const current = preferences.value.sort
  preferences.value.sort = { column, direction: current?.column === column && current.direction === 'asc' ? 'desc' : 'asc' }
}
function sortState(column: number) {
  if (!props.sortable || props.headers[column] === '操作') return undefined
  if (preferences.value.sort?.column !== column) return 'none'
  return preferences.value.sort.direction === 'asc' ? 'ascending' : 'descending'
}
function columnClasses(index: number) {
  return { 'sticky-name': index === 0, 'numeric-cell': props.numericColumns.includes(index), 'highlight-column': props.highlightColumn === index }
}
function cellTone(value: TableCell) {
  if (typeof value !== 'string' || !/^[+-]/.test(value.trim())) return ''
  return value.trim().startsWith('-') ? 'down' : 'up'
}

const columnMenu = ref<HTMLDetailsElement>()
function closeColumns() {
  if (!columnMenu.value?.open) return
  columnMenu.value.open = false
  columnMenu.value.querySelector('summary')?.focus()
}
function dismissColumns(event: PointerEvent) {
  if (event.target instanceof Node && !columnMenu.value?.contains(event.target) && columnMenu.value) columnMenu.value.open = false
}
onMounted(() => document.addEventListener('pointerdown', dismissColumns))
onBeforeUnmount(() => document.removeEventListener('pointerdown', dismissColumns))
</script>
