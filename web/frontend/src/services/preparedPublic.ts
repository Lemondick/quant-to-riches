import { ApiError } from './dataApi.ts'

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, ms)
    if (signal.aborted) abort()
    else signal.addEventListener('abort', abort, { once: true })
  })
}
export async function readPreparedPublic<T>(read: () => Promise<T>, signal: AbortSignal,
  options: { attempts?: number; pause?: (ms: number, signal: AbortSignal) => Promise<void> } = {}): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted()
    try { return await read() } catch (error) {
      signal.throwIfAborted()
      if (!(error instanceof ApiError) || error.status !== 503 || error.code !== 'public_snapshot_unavailable' || attempt >= (options.attempts ?? 24)) throw error
      await (options.pause ?? pause)(attempt < 4 ? 500 : 1500, signal)
    }
  }
}
