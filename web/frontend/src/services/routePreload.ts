type Loaders = Record<string, () => Promise<unknown>>

export function createRoutePreloader(loaders: Loaders) {
  const loaded = new Set<string>()
  const pending = new Map<string, Promise<void>>()
  function preload(path: string): Promise<void> {
    const pathname = path.split(/[?#]/)[0]
    if (!loaders[pathname] || loaded.has(pathname)) return Promise.resolve()
    const previous = pending.get(pathname)
    if (previous) return previous
    const work = loaders[pathname]().then(() => { loaded.add(pathname) }).catch(() => {
      // A failed speculative request must not block ordinary router navigation.
    }).finally(() => { pending.delete(pathname) })
    pending.set(pathname, work)
    return work
  }
  function warm(currentPath: string) {
    let cancelled = false, idle: number | undefined
    const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection
    if (connection?.saveData || /(^|-)2g$/.test(connection?.effectiveType ?? '')) return () => {}
    const timer = setTimeout(() => {
      if (document.visibilityState !== 'visible') return
      const start = () => {
        const queue = Object.keys(loaders).filter(path => path !== currentPath)
        async function worker() {
          while (!cancelled && queue.length) await preload(queue.shift()!)
        }
        // Load code only, with bounded concurrency. No account reads or actions.
        void Promise.all([worker(), worker()])
      }
      if (typeof requestIdleCallback === 'function') idle = requestIdleCallback(start, { timeout: 2000 })
      else start()
    }, 800)
    return () => { cancelled = true; clearTimeout(timer); if (idle !== undefined) cancelIdleCallback(idle) }
  }
  return { preload, warm }
}
