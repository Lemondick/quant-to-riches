import { computed, ref } from 'vue'
import { useLocalState } from './useLocalState.ts'

/** 汇率条目：1 单位 from 币种 = rate 单位本币。来源与时点随行记录，折算展示时必须注明。 */
export type FxRateEntry = {
  from: string
  to: string
  rate: number
  source: string
  observed_at: string
  updated_at: string
}
type StoredFxRate = Omit<FxRateEntry, 'to'> & { to?: string }
const normalizeCurrency = (value: string) => value.trim().toUpperCase()

const currencyCode = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Z]{3}$/.test(value.trim().toUpperCase())
const isFxRateEntry = (value: unknown): value is StoredFxRate => {
  if (typeof value !== 'object' || value === null) return false
  const entry = value as Record<string, unknown>
  return currencyCode(entry.from) && (entry.to === undefined || currencyCode(entry.to)) && typeof entry.rate === 'number' && Number.isFinite(entry.rate) && entry.rate > 0 &&
    typeof entry.source === 'string' && typeof entry.observed_at === 'string' && typeof entry.updated_at === 'string'
}
const isFxRateList = (value: unknown): value is StoredFxRate[] =>
  Array.isArray(value) && value.every(isFxRateEntry)

export type FxPullItem = { from?: unknown; to?: unknown; currency?: unknown; rate?: unknown; source?: unknown; observed_at?: unknown }

export function useFxRates() {
  const baseCurrency = useLocalState('guanlan.fx.base.v1', 'CNY', currencyCode)
  const storedRates = useLocalState<StoredFxRate[]>('guanlan.fx.rates.v1', [], isFxRateList)
  const legacyBase = normalizeCurrency(baseCurrency.value)
  const allRates = computed<FxRateEntry[]>(() => storedRates.value.map(entry => ({
    ...entry, from: normalizeCurrency(entry.from), to: normalizeCurrency(entry.to ?? legacyBase),
  })))
  // Persist each legacy entry's original base before the user can change it.
  if (storedRates.value.some(entry => !entry.to || entry.from !== normalizeCurrency(entry.from) || entry.to !== normalizeCurrency(entry.to)))
    storedRates.value = allRates.value
  const rates = computed(() => allRates.value.filter(entry => entry.to === normalizeCurrency(baseCurrency.value)))
  const pullUrl = useLocalState('guanlan.fx.pull-url.v1', '', (value): value is string => typeof value === 'string')
  const pulling = ref(false)
  const pullError = ref('')
  const pullNotice = ref('')

  const rateOf = (currency: string | null | undefined): FxRateEntry | null => {
    const code = (currency ?? '').trim().toUpperCase()
    if (!code || code === normalizeCurrency(baseCurrency.value)) return null
    return rates.value.find(entry => entry.from.toUpperCase() === code) ?? null
  }

  /** 把 amount（原币种）折算为本币；返回折算值、所用汇率条目与是否缺汇率。 */
  function convert(amount: number, currency: string | null | undefined) {
    const code = (currency ?? '').trim().toUpperCase()
    if (!currencyCode(code)) return { value: amount, rate: null as FxRateEntry | null, missing: true, currency: code }
    if (code === normalizeCurrency(baseCurrency.value)) return { value: amount, rate: null as FxRateEntry | null, missing: false, currency: code }
    const rate = rateOf(code)
    if (!rate) return { value: amount, rate: null as FxRateEntry | null, missing: true, currency: code }
    return { value: amount * rate.rate, rate, missing: false, currency: code }
  }

  function upsertRate(input: { from: string; rate: number; source: string; observed_at: string }) {
    const from = input.from.trim().toUpperCase()
    const to = normalizeCurrency(baseCurrency.value)
    if (!currencyCode(from)) throw new Error('币种须为 3 位字母代码（如 USD、HKD）。')
    if (!currencyCode(to)) throw new Error('本币须为 3 位字母代码（如 CNY、USD）。')
    if (!(input.rate > 0) || !Number.isFinite(input.rate)) throw new Error('汇率须为正数。')
    const entry: FxRateEntry = { from, to, rate: input.rate, source: input.source.trim() || '手动录入', observed_at: input.observed_at, updated_at: new Date().toISOString() }
    const next = allRates.value.filter(item => item.from !== from || item.to !== to)
    storedRates.value = [...next, entry].sort((a, b) => a.from.localeCompare(b.from) || a.to.localeCompare(b.to))
  }

  function removeRate(from: string) {
    storedRates.value = allRates.value.filter(item => item.from !== normalizeCurrency(from) || item.to !== normalizeCurrency(baseCurrency.value))
  }

  /** 从配置的拉取地址获取汇率 JSON（数组），防御性解析后替换本地列表。 */
  async function pullRates() {
    pullError.value = ''
    pullNotice.value = ''
    const url = pullUrl.value.trim()
    if (!url) { pullError.value = '请先填写拉取地址。'; return }
    const pullBase = normalizeCurrency(baseCurrency.value)
    if (!currencyCode(pullBase)) { pullError.value = '本币须为 3 位字母代码（如 CNY、USD）。'; return }
    pulling.value = true
    try {
      const response = await fetch(url, { headers: { Accept: 'application/json' } })
      if (!response.ok) throw new Error(`拉取失败（HTTP ${response.status}）。`)
      const payload: unknown = await response.json()
      const items: FxPullItem[] = Array.isArray(payload) ? payload : Array.isArray((payload as { items?: unknown }).items) ? (payload as { items: FxPullItem[] }).items : []
      const now = new Date().toISOString()
      const parsed: FxRateEntry[] = []
      for (const item of items) {
        const from = String(item.from ?? item.currency ?? '').trim().toUpperCase()
        const rate = Number(item.rate)
        if (!currencyCode(from) || !(rate > 0) || !Number.isFinite(rate) || item.to !== undefined && normalizeCurrency(String(item.to)) !== pullBase) continue
        parsed.push({ from, to: pullBase, rate, source: String(item.source ?? '拉取配置').slice(0, 80), observed_at: String(item.observed_at ?? now), updated_at: now })
      }
      if (!parsed.length) throw new Error('拉取结果中没有可用的汇率条目。')
      const deduped = new Map<string, FxRateEntry>()
      for (const entry of parsed) deduped.set(entry.from, entry)
      storedRates.value = [...allRates.value.filter(entry => entry.to !== pullBase), ...deduped.values()]
        .sort((a, b) => a.from.localeCompare(b.from) || a.to.localeCompare(b.to))
      pullNotice.value = `已从拉取地址更新 ${deduped.size} 条折算为 ${pullBase} 的汇率。`
    } catch (error) {
      pullError.value = error instanceof Error ? error.message : '拉取失败。'
    } finally {
      pulling.value = false
    }
  }

  const sourcesNote = computed(() => {
    if (!rates.value.length) return '暂无汇率配置'
    const latest = rates.value.reduce((a, b) => (a.updated_at > b.updated_at ? a : b))
    return `共 ${rates.value.length} 条 · 最近更新 ${latest.updated_at.slice(0, 16).replace('T', ' ')}（${latest.source}）`
  })

  return { baseCurrency, rates, pullUrl, pulling, pullError, pullNotice, rateOf, convert, upsertRate, removeRate, pullRates, sourcesNote }
}
