import { computed, onMounted, onScopeDispose, ref, shallowRef } from 'vue'
import { apiCache } from '../services/apiCache'
import { ApiError, createDataApi, clearEtagCache, type BackendSession, type AccountReader } from '../services/dataApi'
import { useApiLoader } from './useApiLoader'

const base = import.meta.env.VITE_API_BASE_URL ?? ''
const api = createDataApi(base)
export const activeBackendSession = shallowRef<BackendSession>()

let previousScope: string | undefined
let sessionPending: Promise<BackendSession> | undefined
export type ScopedReader = AccountReader

export function useAccountResource<T>(name: string, read: (api: ScopedReader, signal: AbortSignal, userId: string) => Promise<T>, ttl = 15000) {
  const session = shallowRef<BackendSession>(), sessionError = shallowRef<unknown>()
  const scope = ref<string | null>(null), mutating = ref(false)
  let disposed = false
  const client: ScopedReader = { async read<R>(path: string, signal?: AbortSignal): Promise<R> {
    const current = session.value?.cache_scope
    if (!current || !scope.value) throw new ApiError(401, '请先登录后端账户。')
    const result = await api.read<R>(path, signal, current)
    if (disposed || session.value?.cache_scope !== current) throw new ApiError(401, '账户会话已改变。')
    return result
  }, async post<R>(path: string, body: unknown = {}): Promise<R> {
    const current = session.value
    if (!current || !scope.value) throw new ApiError(401, '请先登录后端账户。')
    const result = await api.write<R>(path, body, current.csrf_token, current.cache_scope)
    if (disposed || session.value?.cache_scope !== current.cache_scope) throw new ApiError(401, '账户会话已改变。')
    return result
  } }
  const loader = useApiLoader<T>(computed(() => scope.value ? `${base}:private:${scope.value}:${name}` : null), signal => read(client, signal, session.value!.user.id), ttl)
  async function verifySession() {
    scope.value = null; sessionError.value = undefined
    try {
      sessionPending ??= api.read<BackendSession>('/auth/session').finally(() => { sessionPending = undefined })
      const current = await sessionPending
      if (disposed) return
      if (!current.user?.id || !current.cache_scope || !current.csrf_token) throw new Error('会话信息无效，请重新登录。')
      const next = `${current.user.id}:${current.cache_scope}`
      if (previousScope && previousScope !== next) { apiCache.invalidate(); clearEtagCache() }
      previousScope = next; session.value = current; activeBackendSession.value = current; scope.value = next
    } catch (error) { if (!disposed) { apiCache.invalidate(); clearEtagCache(); session.value = undefined; activeBackendSession.value = undefined; sessionError.value = error } }
  }
  const recheck = () => { void verifySession() }
  const reload = () => { if (scope.value) void loader.refresh() }
  onMounted(() => { void verifySession(); window.addEventListener('focus', recheck); window.addEventListener('quant-auth-invalidated', recheck); window.addEventListener('quant-account-updated', reload) })
  onScopeDispose(() => { disposed = true; window.removeEventListener('focus', recheck); window.removeEventListener('quant-auth-invalidated', recheck); window.removeEventListener('quant-account-updated', reload) })
  async function mutate<R>(path: string, body: unknown, method = 'POST'): Promise<R> {
    const current = session.value
    if (!current || !scope.value) throw new ApiError(401, '请先登录后端账户。')
    mutating.value = true
    try {
      const result = await api.write<R>(path, body, current.csrf_token, current.cache_scope, method)
      if (disposed || session.value?.cache_scope !== current.cache_scope) throw new ApiError(401, '账户会话已改变。')
      apiCache.markStale(`${base}:private:`)
      window.dispatchEvent(new Event('quant-account-updated'))
      await loader.refresh()
      return result
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.status)) window.dispatchEvent(new Event('quant-auth-invalidated'))
      throw error
    } finally { mutating.value = false }
  }
  return { ...loader, session, mutating, mutate, read: client.read, error: computed(() => sessionError.value ?? loader.error.value), refresh: async () => {
    if (!scope.value || sessionError.value instanceof ApiError || loader.error.value instanceof ApiError && [401, 403].includes(loader.error.value.status)) await verifySession()
    else await loader.refresh()
  } }
}
