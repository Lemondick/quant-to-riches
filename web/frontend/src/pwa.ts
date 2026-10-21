/** PWA 注册与离线同步时点（batch5-C#5，手写精简版 SW）。 */

const LAST_SYNC_KEY = 'guanlan.pwa.last_sync'

type SwMessage = { type: 'SYNC_OK' | 'OFFLINE_SERVE'; at: number }

/** 注册 /sw.js（开发模式跳过，避免缓存干扰调试）；监听 SW 消息更新同步时点。 */
export function initPwa() {
  if (!('serviceWorker' in navigator)) return
  if (import.meta.env.DEV) return
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* 静默失败：PWA 为渐进增强，不影响正常使用。 */
    })
  })
  navigator.serviceWorker.addEventListener('message', (event: MessageEvent<SwMessage>) => {
    const data = event.data
    if (data && typeof data.at === 'number' && (data.type === 'SYNC_OK' || data.type === 'OFFLINE_SERVE')) {
      try {
        localStorage.setItem(LAST_SYNC_KEY, String(data.at))
      } catch {
        /* 本地存储不可用时忽略，横幅降级为无时间显示。 */
      }
    }
  })
}

/** 最近一次成功同步（或离线命中缓存）的时间戳，供离线横幅"更新于 HH:mm"使用。 */
export function lastSyncTime(): number | null {
  try {
    const raw = localStorage.getItem(LAST_SYNC_KEY)
    const value = raw === null ? NaN : Number(raw)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}
