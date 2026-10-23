export class ApiError extends Error {
  status: number
  code?: string
  constructor(status: number, message: string, code?: string) { super(message); this.status = status; this.code = code }
}

/**
 * batch4 流 B（#8）：ETag 条件请求缓存。
 * 后端对公开数据类 GET 接口返回 ETag（QUANT_ETAG_ENABLED，默认开）；
 * 这里在 GET 时自动带 If-None-Match，304 到达时直接用缓存数据，避免重复下行。
 * key 为完整请求 URL；只缓存 GET；容量 200，FIFO 淘汰。
 */
type ETagEntry = { etag: string; data: unknown }
const etagCache = new Map<string, ETagEntry>()
const ETAG_CACHE_CAPACITY = 200
function etagRemember(url: string, etag: string, data: unknown) {
  if (etagCache.size >= ETAG_CACHE_CAPACITY) {
    const oldest = etagCache.keys().next()
    if (!oldest.done) etagCache.delete(oldest.value)
  }
  etagCache.set(url, { etag, data })
}
/** 登录/会话切换时调用，避免跨会话复用条件缓存。 */
export function clearEtagCache() { etagCache.clear() }
export function apiRoot(base: string) {
  const value = base.trim().replace(/\/+$/, '')
  if (!value) return ''
  return value.endsWith('/api/v1') ? value : value.endsWith('/api') ? `${value}/v1` : `${value}/api/v1`
}
export function createDataApi(base: string, fetcher: typeof fetch = fetch) {
  const root = apiRoot(base)
  async function request<T>(path: string, signal?: AbortSignal, body?: unknown, method = body === undefined ? 'GET' : 'POST', csrf?: string, scope?: string): Promise<T> {
    path = path.replace(/^\/api\/v1(?=\/|\?|$)/, '') || '/'
    if (!root) throw new ApiError(0, '后端服务尚未接入。')
    const controller = new AbortController()
    const abort = () => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) controller.abort()
    // Strategy validation/preview runs within the gateway's 60-second job lease.
    const timeoutMs = /^\/research\/strategy-settings(?:\/preview)?$/.test(path) ? 65000 : 15000
    const timeout = setTimeout(abort, timeoutMs)
    const url = `${root}${path}`
    const isGet = body === undefined
    const conditional = isGet ? etagCache.get(url) : undefined
    async function perform(allowConditional: boolean): Promise<T> {
      const entry = allowConditional ? conditional : undefined
      const response = await fetcher(url, { signal: controller.signal, credentials: 'same-origin', redirect: 'error', method, headers: { Accept: 'application/json', 'X-Trace-Id': crypto.randomUUID().replaceAll('-', ''), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(csrf ? { 'X-CSRF-Token': csrf } : {}), ...(scope ? { 'X-Session-Scope': scope } : {}), ...(entry ? { 'If-None-Match': entry.etag } : {}) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
      if (response.status === 304) {
        // 后端确认数据未变：用缓存。理论上不会无缓存收到 304，防御性地去掉条件头重试一次。
        if (entry) return structuredClone(entry.data) as T
        if (allowConditional) return perform(false)
        throw new ApiError(304, '数据未变化但本地无缓存，请重试。')
      }
      if (!response.ok) {
        const failure = await response.json().catch(() => undefined)
        const code = failure?.error_code ?? failure?.error?.code ?? failure?.code
        throw new ApiError(response.status, path === '/auth/2fa/verify' && response.status === 401 ? '验证码或恢复码无效，或验证已过期。请重试或返回密码登录。' : response.status === 401 ? path === '/auth/login' ? '账号或密码无效。' : '请先登录后端账户。' : response.status === 403 ? '当前账户没有访问权限。' : response.status === 503 ? '数据暂不可用，后台准备完成后可再次刷新。' : `读取失败（HTTP ${response.status}），请重试。`, typeof code === 'string' ? code : undefined)
      }
      const data = await response.json() as T
      if (isGet) {
        const etag = response.headers.get('ETag')
        if (etag) etagRemember(url, etag, data)
        else etagCache.delete(url)
      }
      return data
    }
    try {
      return await perform(true)
    } catch (error) {
      if (controller.signal.aborted) throw new ApiError(0, signal?.aborted ? '读取已取消。' : '读取超时，请稍后重试。')
      throw error
    } finally { clearTimeout(timeout); signal?.removeEventListener('abort', abort) }
  }
  return { read: <T>(path: string, signal?: AbortSignal, scope?: string) => request<T>(path, signal, undefined, 'GET', undefined, scope), write: <T>(path: string, body: unknown, csrf: string, scope: string, method = 'POST', signal?: AbortSignal) => request<T>(path, signal, body, method, csrf, scope), login: (username: string, password: string) => request<LoginResult>('/auth/login', undefined, { username, password }), verifyTwoFactor: (pendingToken: string, code: string) => request<LoginSession>('/auth/2fa/verify', undefined, { pending_token: pendingToken, code }) }
}
export type LoginSession = { user: { id: string; username: string; role: string }; csrf_token: string; expires_at: string }
export type LoginResult = LoginSession | { two_factor_required: true; pending_token: string }
export type AccountReader = { read: <T>(path: string, signal?: AbortSignal) => Promise<T>; post: <T>(path: string, body?: unknown) => Promise<T> }
export type BackendSession = { user: { id: string; username: string; role: 'admin' | 'user' }; csrf_token: string; cache_scope: string }

export type Position = { symbol: string; asset_type: string; name: string; quantity: string; average_cost: string; market_value: string; as_of_date: string }
export type Observation = { updated_at?: string | null; source_time?: string | null; fetched_at?: string | null; quality_status?: string; is_stale?: boolean; market?: string; market_timezone?: string | null; market_session?: string; calendar_revision?: string | null; calendar_source?: string | null; quality_reason?: string | null; auction_supported?: boolean }
export type Quote = Observation & { id?: string | null; symbol: string; name: string; asset_type: string; price: string; change?: string; change_rate: string; observed_at: string | null; source: string; currency?: string | null; currency_basis?: string | null; calendar_id?: string | null; source_symbol?: string | null; raw_source_time?: string | null; change_basis?: string | null; cache_fallback?: boolean; cache_read_at?: string | null }
export type IndexProviderMapping = { provider: string; symbol: string; response_symbol: string; response_names: string[]; response_type: string; response_market: string; response_currency: string | null; currency_basis: string; source_timezone: string; verification_status: 'verified' | 'pending' | 'unavailable'; verified_at: string; evidence_urls: string[] }
export type IndexDefinition = { id: string; name: string; official_name: string; market: string; currency: string; timezone: string; calendar_id: string; exchange_code: string; display_order: number; enabled: boolean; product_status: 'confirmed' | 'provisional'; product_evidence: string; quote: IndexProviderMapping; history: IndexProviderMapping | null; legacy_history_symbol: string | null }
export type IndexQuoteStatus = Observation & { id: string; quality_status: string; quality_reason: string | null; source: string; source_time: string | null; fetched_at: string | null; cache_read_at: string; fallback_used?: boolean; fallback_reason?: string | null }
export type IndexHistory = Observation & { id: string; index_id?: string; symbol: string; name: string; currency?: string; items: { date: string; open: string; high: string; low: string; close: string; volume?: string; is_partial?: boolean }[]; source: string; source_quote_time?: string | null; last_bar_partial?: boolean; omitted_incomplete_bars?: number; volume_status?: string; history_count?: number; history_start?: string; history_end?: string; observed_at?: string | null; stale: boolean }
export type Valuation = Observation & { symbol: string; estimate_nav: string | null; official_nav?: string | null; official_change_rate?: string | null; change_rate: string | null; observed_at: string | null; source: string; nav_date: string | null; valuation_type: 'intraday_estimate' | 'latest_nav' }
export type MarketBreadth = Observation & { advancing: number; declining: number; unchanged: number; limit_up: number; limit_down: number; observed_at: string; source: string; turnover: string | null; previous_turnover: string | null; turnover_ratio: string | null; previous_trade_date: string | null; stale: boolean; unavailable: string[] }
export type MarketSector = Observation & { name: string; code: string; change_rate: string | null; leader?: string | null; category?: string; source?: string; observed_at?: string | null; fetched_at?: string | null; turnover?: string | null; advancing?: number | null; declining?: number | null; members?: number | null }
export type SectorCatalog = Observation & { listed_total?: number | null; listed_received?: number | null; unquoted?: number; category: string; source: string; total: number | null; received: number; complete: boolean | null; fetched_at: string | null; observed_at: string | null; stale: boolean; error: string | null; coverage_version?: number; catalog_status?: string; catalog_fetched_at?: string | null; catalog_http_ok?: boolean | null; catalog_parse_ok?: boolean | null; catalog_cache_used?: boolean; catalog_stale?: boolean; catalog_error?: string | null; quote_total?: number | null; quote_payload_complete?: boolean | null; quotes_complete?: boolean | null; http_ok?: boolean | null; parse_ok?: boolean | null; source_time_known?: boolean; cache_used?: boolean | null }
export type MarketSnapshot = { registry_version?: string; index_registry?: IndexDefinition[]; index_statuses?: IndexQuoteStatus[]; sector_catalogs?: SectorCatalog[]; stale_sections?: string[]; market_session?: string; market_sessions?: Record<string, string>; refresh_active_markets?: string[]; refresh_interval_seconds?: number; indices: Quote[]; fund_rankings: (Observation & { symbol: string; name: string; unit_nav: string; change_rate: string; nav_date: string | null })[]; sector_rankings: MarketSector[]; breadth?: MarketBreadth | null; observed_at: string; stale: boolean; unavailable: string[] }
export type DashboardSummary = {
  user_id: string; generated_at: string; unavailable: string[]
  portfolio: { positions: Position[]; total_value: string; cash: string; as_of_date: string | null }
  preferences: { watchlist: { symbol: string; name: string; asset_type: string }[] }
  valuations: { items: Valuation[]; unavailable: string[] }
  quotes: { items: Quote[]; unavailable: string[] }
  market: MarketSnapshot | null
}

export function validateDashboard(value: DashboardSummary, userId: string) {
  if (value?.user_id && value.user_id !== userId) throw new ApiError(401, '账户会话已改变，请重新读取。')
  if (!value || value.user_id !== userId || !Array.isArray(value.portfolio?.positions) || !Array.isArray(value.preferences?.watchlist) || !Array.isArray(value.valuations?.items) || !Array.isArray(value.quotes?.items)) throw new Error('账户数据格式不正确，请重新读取。')
  return value
}
