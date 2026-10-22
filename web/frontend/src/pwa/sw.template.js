/* global __GUANLAN_PRECACHE_ASSETS__ */
/* 应用壳与构建资源可离线使用。API 请求始终交给网络，禁止持久保存会话或账户响应。 */
'use strict'

const PRECACHE = 'guanlan-precache-v1'
const PRECACHE_ASSETS = __GUANLAN_PRECACHE_ASSETS__

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(PRECACHE).then((cache) => cache.addAll(PRECACHE_ASSETS)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  // 包括旧 guanlan-api-* 在内的历史缓存必须删除，避免升级后遗留私有响应。
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((key) => key !== PRECACHE).map((key) => caches.delete(key))))
    .then(() => self.clients.claim()))
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)
  if (/^\/api\/v1(?:\/|$)/.test(url.pathname)) return
  if (request.method !== 'GET' || url.origin !== self.location.origin) return

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(async () => (await (await caches.open(PRECACHE)).match('/')) || Response.error()))
    return
  }

  // 只匹配构建时列出的静态资源；其他 URL 不进入应用壳缓存。
  if (PRECACHE_ASSETS.some((path) => new URL(path, self.location.origin).href === url.href)) {
    event.respondWith(caches.open(PRECACHE).then(async (cache) => (await cache.match(request)) || fetch(request)))
  }
})
