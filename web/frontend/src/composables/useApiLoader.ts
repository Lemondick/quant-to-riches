import { onScopeDispose, ref, shallowRef, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { apiCache } from '../services/apiCache'
import { ApiError } from '../services/dataApi'

export function useApiLoader<T>(key: MaybeRefOrGetter<string | null>, load: (signal: AbortSignal, key: string) => Promise<T>, ttl = 15000, pollPublicStale = false, pollInterval = 60000) {
  const data = shallowRef<T>(), error = shallowRef<unknown>(), refreshing = ref(false), stale = ref(false)
  let unsubscribe = () => {}
  let retry: ReturnType<typeof setTimeout> | undefined, attempts = 0
  let staleRetry: ReturnType<typeof setTimeout> | undefined, staleAttempts = 0
  function update() {
    const current = toValue(key)
    if (!current) { data.value = undefined; error.value = undefined; refreshing.value = false; stale.value = false; return }
    const value = apiCache.peek<T>(current, ttl, Math.max(ttl * 12, 300000))
    data.value = value.data; error.value = value.error; refreshing.value = value.loading; stale.value = value.stale
    // Public stale responses are usable immediately. Recheck their replacement
    // within seconds rather than holding "updating" for the full cache TTL.
    if (pollPublicStale && (value.data as { stale?: boolean } | undefined)?.stale === true && !value.loading && !value.error && staleRetry === undefined && staleAttempts < 20) {
      staleRetry = setTimeout(() => { staleRetry = undefined; staleAttempts++; void refresh(true) }, staleAttempts < 10 ? 1000 : 3000)
    }
    if ((value.data as { stale?: boolean } | undefined)?.stale === false) { clearTimeout(staleRetry); staleRetry = undefined; staleAttempts = 0 }
  }
  async function refresh(force = true) {
    const current = toValue(key)
    if (current) await apiCache.revalidate(current, signal => load(signal, current), ttl, force).catch(failure => {
      if (toValue(key) !== current) return
      if (failure instanceof ApiError && failure.status === 401) {
        apiCache.invalidate()
        window.dispatchEvent(new Event('quant-auth-invalidated'))
      }
      if (failure instanceof ApiError && failure.status === 403) apiCache.invalidate(current)
      error.value = failure
      if (failure instanceof ApiError && failure.status === 503 && data.value === undefined && attempts < 5) {
        clearTimeout(retry)
        retry = setTimeout(() => { void refresh(false) }, Math.min(2000 * 2 ** attempts++, 30000))
      }
    })
  }
  const stop = watch(() => toValue(key), current => {
    unsubscribe(); clearTimeout(retry); clearTimeout(staleRetry); staleRetry = undefined; staleAttempts = 0; attempts = 0; update()
    if (current) { unsubscribe = apiCache.subscribe(current, update); void refresh(false) }
  }, { immediate: true })
  const visibleRefresh = () => { if (document.visibilityState === 'visible') void refresh(false) }
  const timer = setInterval(visibleRefresh, Math.max(ttl, pollInterval))
  if (pollInterval < 60000) document.addEventListener('visibilitychange', visibleRefresh)
  onScopeDispose(() => { stop(); unsubscribe(); clearInterval(timer); clearTimeout(retry); clearTimeout(staleRetry); document.removeEventListener('visibilitychange', visibleRefresh) })
  return { data, error, refreshing, stale, refresh }
}
