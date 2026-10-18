export type CacheState<T> = { data: T | undefined; error: unknown; loading: boolean; stale: boolean; updatedAt: number }
type Entry = { data?: unknown; error?: unknown; updatedAt: number; invalidated: boolean; pending?: Promise<unknown>; controller?: AbortController; generation: number; listeners: Set<() => void> }

export function createSWRCache(now = Date.now, capacity = 128) {
  const entries = new Map<string, Entry>()
  function entry(key: string) {
    let value = entries.get(key)
    if (!value) {
      if (entries.size >= capacity) {
        const candidate = [...entries].find(([, item]) => item.listeners.size === 0)
        if (candidate) { candidate[1].controller?.abort(); entries.delete(candidate[0]) }
      }
      value = { updatedAt: 0, invalidated: false, generation: 0, listeners: new Set() }
      entries.set(key, value)
    }
    return value
  }
  function emit(value: Entry) { for (const listener of value.listeners) listener() }
  return {
    peek<T>(key: string, ttl = 15000, maxStale = 300000): CacheState<T> {
      const value = entry(key)
      const expired = value.data !== undefined && now() - value.updatedAt > maxStale
      return { data: value.data === undefined || expired ? undefined : structuredClone(value.data) as T, error: value.error, loading: !!value.pending, stale: value.data !== undefined && (value.invalidated || value.error !== undefined || now() - value.updatedAt >= ttl), updatedAt: value.updatedAt }
    },
    subscribe(key: string, listener: () => void) {
      const value = entry(key)
      value.listeners.add(listener)
      return () => { value.listeners.delete(listener) }
    },
    revalidate<T>(key: string, load: (signal: AbortSignal) => Promise<T>, ttl = 15000, force = false): Promise<T> {
      const value = entry(key)
      if (value.pending) return value.pending as Promise<T>
      if (!force && !value.invalidated && value.data !== undefined && now() - value.updatedAt < ttl) return Promise.resolve(structuredClone(value.data) as T)
      const controller = new AbortController()
      const generation = ++value.generation
      value.controller = controller
      value.error = undefined
      const pending = Promise.resolve().then(() => load(controller.signal)).then(data => {
        if (value.generation === generation) { value.data = structuredClone(data); value.updatedAt = now(); value.invalidated = false }
        return data
      }).catch(error => {
        if (value.generation === generation && !controller.signal.aborted) value.error = error
        throw error
      }).finally(() => {
        if (value.generation === generation) { value.pending = undefined; value.controller = undefined; emit(value) }
      })
      value.pending = pending
      emit(value)
      return pending
    },
    markStale(prefix: string) {
      for (const [key, value] of entries) {
        if (!key.startsWith(prefix)) continue
        value.generation++
        value.controller?.abort()
        value.pending = undefined
        value.controller = undefined
        value.error = undefined
        value.invalidated = true
        emit(value)
      }
    },
    invalidate(prefix = '') {
      for (const [key, value] of entries) {
        if (!key.startsWith(prefix)) continue
        value.generation++
        value.controller?.abort()
        value.pending = undefined
        value.data = undefined
        value.error = undefined
        value.updatedAt = 0
        value.invalidated = false
        emit(value)
        if (!value.listeners.size) entries.delete(key)
      }
    },
  }
}

export const apiCache = createSWRCache()
