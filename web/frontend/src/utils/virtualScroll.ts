/** 虚拟滚动核心：纯函数部分（无 DOM，可单元测试）。 */
export type VirtualRange = { start: number; end: number } // end 为开区间

/** 计算可视窗口 [start, end)，含上下 overscan 缓冲行。 */
export function computeVisibleRange(
  scrollTop: number,
  viewportHeight: number,
  rowHeight: number,
  itemCount: number,
  overscan = 5,
): VirtualRange {
  if (rowHeight <= 0 || itemCount <= 0 || viewportHeight <= 0) return { start: 0, end: 0 }
  const top = Math.max(0, scrollTop)
  const start = Math.max(0, Math.floor(top / rowHeight) - overscan)
  const end = Math.min(itemCount, Math.ceil((top + viewportHeight) / rowHeight) + overscan)
  return { start: Math.min(start, end), end }
}

/** 可视窗口内实际渲染的行数。 */
export function renderedRowCount(range: VirtualRange): number {
  return Math.max(0, range.end - range.start)
}

/** 总高度（spacer 占位用）。 */
export function totalContentHeight(itemCount: number, rowHeight: number): number {
  return Math.max(0, itemCount) * Math.max(0, rowHeight)
}
