/* 观澜 PWA service worker（手写精简版，batch5-C#5）。
 * 构建时由 vite.config.ts 的 guanlanPwaSw 插件把 __GUANLAN_PRECACHE_ASSETS__
 * 替换为本次构建产出的 js/css 资源列表（hash 文件名即版本），写入 dist/sw.js。
 * 缓存策略：
 *   - 应用壳（/ + 构建资源）：安装时预缓存；导航请求 network-first，失败回退缓存。
 *   - GET /api/v1/*（持仓/净资产等）：stale-while-revalidate；离线无缓存时返回 503 offline。
 *   - 成功同步 / 离线命中时向页面 postMessage，页面据此更新"离线数据 · 更新于 HH:mm"。
 */
'use strict'

const PRECACHE = 'guanlan-precache-v1'
const RUNTIME_API = 'guanlan-api-v1'
const PRECACHE_ASSETS = __GUANLAN_PRECACHE_ASSETS__

function broadcast(message) {
  self.clients.matchAll({ includeUncontrolled: true, type: 'window' }).then((clients) => {
    for (const client of clients) client.postMessage(message)
  })
}

function isApiGet(request) {
  if (request.method !== 'GET') return false
  try {
    return new URL(request.url).pathname.startsWith('/api/v1/')
  } catch {
    return false
  }
}

function offlineJson() {
  return new Response(JSON.stringify({ error: { code: 'offline', message: '离线，暂无缓存数据' } }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  })
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(PRECACHE).then((cache) => cache.addAll(PRECACHE_ASSETS)).then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== PRECACHE && key !== RUNTIME_API).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event

  // 1) API GET：stale-while-revalidate
  if (isApiGet(request)) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(RUNTIME_API)
        const cached = await cache.match(request)
        const network = fetch(request)
          .then(async (response) => {
            if (response && response.ok) {
              await cache.put(request, response.clone())
              broadcast({ type: 'SYNC_OK', at: Date.now() })
            }
            return response
          })
          .catch(() => null)
        if (cached) {
          event.waitUntil(
            network.then((response) => {
              if (!response) broadcast({ type: 'OFFLINE_SERVE', at: Date.now() })
            }),
          )
          return cached
        }
        const response = await network
        if (response) return response
        broadcast({ type: 'OFFLINE_SERVE', at: Date.now() })
        return offlineJson()
      })(),
    )
    return
  }

  // 2) 导航请求：network-first，离线回退应用壳
  if (request.mode === 'navigate') {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request)
          const cache = await caches.open(PRECACHE)
          if (response && response.ok) await cache.put('/', response.clone())
          return response
        } catch {
          const cache = await caches.open(PRECACHE)
          return (await cache.match('/')) || Response.error()
        }
      })(),
    )
    return
  }

  // 3) 其他同源静态资源：cache-first
  if (request.method === 'GET' && new URL(request.url).origin === self.location.origin) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(PRECACHE)
        const cached = await cache.match(request)
        if (cached) return cached
        try {
          const response = await fetch(request)
          if (response && response.ok) await cache.put(request, response.clone())
          return response
        } catch {
          return Response.error()
        }
      })(),
    )
  }
})
