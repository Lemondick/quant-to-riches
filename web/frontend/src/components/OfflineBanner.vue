<template>
  <div v-if="!online" class="offline-banner" role="status">
    <WifiOff :size="14" aria-hidden="true" />
    <span>离线数据<span v-if="lastSyncLabel"> · 更新于 {{ lastSyncLabel }}</span></span>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { WifiOff } from 'lucide-vue-next'
import { useOnline } from '../composables/useOnline'

const { online, lastSync } = useOnline()
const lastSyncLabel = computed(() => {
  if (!lastSync.value) return null
  const date = new Date(lastSync.value)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
})
</script>
<style scoped>
.offline-banner{display:flex;align-items:center;gap:8px;padding:9px 16px;background:rgba(245,185,66,.12);border-bottom:1px solid rgba(245,185,66,.35);color:#f5cf7a;font-size:13px}
</style>
