import { onBeforeUnmount, onMounted, ref } from 'vue'
import { lastSyncTime } from '../pwa'

/** 在线状态 + 最近同步时点（供离线横幅使用）。 */
export function useOnline() {
  const online = ref(typeof navigator === 'undefined' ? true : navigator.onLine)
  const lastSync = ref<number | null>(lastSyncTime())
  function update() {
    online.value = navigator.onLine
    lastSync.value = lastSyncTime()
  }
  onMounted(() => {
    update()
    window.addEventListener('online', update)
    window.addEventListener('offline', update)
  })
  onBeforeUnmount(() => {
    window.removeEventListener('online', update)
    window.removeEventListener('offline', update)
  })
  return { online, lastSync }
}
