export type Observation = { updated_at?: string | null; quality_status?: string; is_stale?: boolean }

export function updateTime(value?: string | null) {
  const unknown = '更新时间：未知'
  if (!value) return unknown
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const parsed = new Date(value)
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value ? `更新时间：${value}` : unknown
  }
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return unknown
  const parsed = new Date(value)
  if (!Number.isFinite(parsed.getTime())) return unknown
  const display = new Date(parsed.getTime() + 8 * 3600000).toISOString().replace('T', ' ')
  return '更新时间：' + display.slice(0, /^.{16}(?:Z|[+-])/.test(value) ? 16 : 19)
}

export function oldestUpdate(rows: Observation[]) {
  if (!rows.length || rows.some(row => !row.updated_at || updateTime(row.updated_at) === '更新时间：未知')) return null
  return [...rows].sort((a, b) => Date.parse(a.updated_at!) - Date.parse(b.updated_at!))[0]!.updated_at!
}

export function usableEstimate(value: ({ valuation_type: string; estimate_nav: string | null; change_rate: string | null } & Observation) | null | undefined): boolean {
  return !!value && value.valuation_type === 'intraday_estimate' && value.quality_status === 'valid' && !value.is_stale && value.estimate_nav !== null && value.change_rate !== null && Number(value.estimate_nav) > 0 && Number.isFinite(Number(value.change_rate))
}
