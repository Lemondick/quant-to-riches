/**
 * v-virtual-scroll 轻量虚拟滚动指令（自研，不引入新依赖）。
 * 原理：定高行 + 绝对定位渲染可视窗口。
 *
 * 用法：
 *   <div v-virtual-scroll="binding" class="my-scroller"></div>
 * 其中 binding = {
 *   items: T[],            // 数据
 *   rowHeight: number,     // 每行固定高度（px），必填
 *   overscan?: number,     // 上下缓冲行数，默认 5
 *   renderRow: (item, index) => string | Node,  // 行内容（string 视为 HTML，Node 直接挂载）
 *   rowClass?: string | ((item, index) => string),
 *   onRange?: (range: { start, end }) => void,  // 可视窗口变化回调（性能观测用）
 * }
 * 容器高度由调用方通过 CSS 决定（需可滚动：overflow-y auto 由指令自动设置）。
 */
import type { Directive, DirectiveBinding } from 'vue'
import { computeVisibleRange, totalContentHeight, type VirtualRange } from '../utils/virtualScroll'

export type VirtualScrollBinding<T = unknown> = {
  items: T[]
  rowHeight: number
  overscan?: number
  renderRow: (item: T, index: number) => string | Node
  rowClass?: string | ((item: T, index: number) => string)
  onRange?: (range: VirtualRange) => void
}

type ScrollerState = {
  spacer: HTMLDivElement
  viewport: HTMLDivElement
  onScroll: () => void
  raf: number | null
  binding: VirtualScrollBinding
  lastKey: string
}

function rowClassOf(binding: VirtualScrollBinding, item: unknown, index: number): string {
  const rc = binding.rowClass
  if (typeof rc === 'function') return (rc as (item: unknown, index: number) => string)(item, index)
  return rc ?? ''
}

function renderRange(el: HTMLElement, state: ScrollerState) {
  const { binding, viewport } = state
  const items = binding.items ?? []
  const range = computeVisibleRange(el.scrollTop, el.clientHeight, binding.rowHeight, items.length, binding.overscan ?? 5)
  state.spacer.style.height = `${totalContentHeight(items.length, binding.rowHeight)}px`
  // 仅在窗口变化时重建，避免滚动抖动时反复刷 DOM
  const key = `${range.start}:${range.end}:${items.length}:${binding.rowHeight}`
  if (key === state.lastKey) return
  state.lastKey = key
  viewport.textContent = ''
  const fragment = document.createDocumentFragment()
  for (let index = range.start; index < range.end; index++) {
    const item = items[index]
    const row = document.createElement('div')
    row.className = `vscroll-row ${rowClassOf(binding, item, index)}`.trim()
    row.style.cssText = `position:absolute;left:0;right:0;top:${index * binding.rowHeight}px;height:${binding.rowHeight}px;overflow:hidden`
    row.dataset.index = String(index)
    const content = binding.renderRow(item, index)
    if (typeof content === 'string') row.innerHTML = content
    else row.appendChild(content)
    fragment.appendChild(row)
  }
  viewport.appendChild(fragment)
  binding.onRange?.(range)
}

function scheduleRender(el: HTMLElement, state: ScrollerState) {
  if (state.raf !== null) return
  state.raf = requestAnimationFrame(() => {
    state.raf = null
    renderRange(el, state)
  })
}

function readBinding(binding: DirectiveBinding): VirtualScrollBinding {
  const value = (binding.value ?? {}) as VirtualScrollBinding
  return {
    items: Array.isArray(value.items) ? value.items : [],
    rowHeight: Number(value.rowHeight) > 0 ? Number(value.rowHeight) : 36,
    overscan: value.overscan ?? 5,
    renderRow: typeof value.renderRow === 'function' ? value.renderRow : () => '',
    rowClass: value.rowClass,
    onRange: value.onRange,
  }
}

export const virtualScroll: Directive<HTMLElement, VirtualScrollBinding> = {
  mounted(el, binding) {
    el.style.position = 'relative'
    el.style.overflowY = 'auto'
    el.style.contain = 'strict'
    const spacer = document.createElement('div')
    spacer.style.cssText = 'position:relative;width:1px'
    spacer.setAttribute('aria-hidden', 'true')
    const viewport = document.createElement('div')
    viewport.style.cssText = 'position:absolute;top:0;left:0;right:0'
    viewport.setAttribute('role', 'presentation')
    el.appendChild(spacer)
    el.appendChild(viewport)
    const state: ScrollerState = {
      spacer, viewport, raf: null, binding: readBinding(binding), lastKey: '',
      onScroll: () => scheduleRender(el, (el as unknown as { __vscroll: ScrollerState }).__vscroll),
    }
    ;(el as unknown as { __vscroll: ScrollerState }).__vscroll = state
    el.addEventListener('scroll', state.onScroll, { passive: true })
    renderRange(el, state)
  },
  updated(el, binding) {
    const state = (el as unknown as { __vscroll?: ScrollerState }).__vscroll
    if (!state) return
    state.binding = readBinding(binding)
    state.lastKey = ''
    renderRange(el, state)
  },
  unmounted(el) {
    const state = (el as unknown as { __vscroll?: ScrollerState }).__vscroll
    if (!state) return
    el.removeEventListener('scroll', state.onScroll)
    if (state.raf !== null) cancelAnimationFrame(state.raf)
    delete (el as unknown as { __vscroll?: ScrollerState }).__vscroll
  },
}

export default virtualScroll
