<template>
  <div class="app-shell">
    <Sidebar />
    <div class="main-shell">
      <Topbar />
      <div v-if="!localStorageAvailable" class="storage-notice" role="status">浏览器限制了本地保存，当前更改仅在本次打开期间保留。请检查站点存储权限。</div>
      <main class="page-shell">
        <RouterView />
      </main>
    </div>
    <AskWidget />
  </div>
</template>

<script setup lang="ts">
import { defineAsyncComponent, onMounted, onBeforeUnmount, watch } from 'vue'
import router, { warmRouteComponents } from './router'
import Sidebar from './components/Sidebar.vue'
import Topbar from './components/Topbar.vue'
// batch4-B#9：问答浮窗不阻塞首屏，异步加载（连带 batch2Api/mock 数据移出 entry）。
const AskWidget = defineAsyncComponent(() => import('./components/AskWidget.vue'))
import { usePreferences } from './composables/usePreferences'
import { localStorageAvailable } from './composables/useLocalState'
import { setDisplayTimezone } from './utils/format'
const { preferences } = usePreferences()
let stopWarmup: (() => void) | undefined
onMounted(() => { stopWarmup = warmRouteComponents(router.currentRoute.value.path) })
onBeforeUnmount(() => { stopWarmup?.() })
watch(preferences, value => {
  document.documentElement.dataset.marketColors = value.marketColors
  document.documentElement.dataset.appearance = value.appearance
  document.documentElement.dataset.motion = value.reducedMotion ? 'reduced' : 'standard'
  setDisplayTimezone(value.timezone)
}, { deep: true, immediate: true })
</script>

<style>
.page-transition-wrap {
  width: 100%;
  min-width: 0;
}
.page-enter-active {
  animation: pageIn .35s ease both;
}
.page-leave-active {
  animation: pageOut .2s ease both;
}
@keyframes pageIn {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes pageOut {
  from { opacity: 1; transform: translateY(0); }
  to { opacity: 0; transform: translateY(-8px); }
}
</style>
