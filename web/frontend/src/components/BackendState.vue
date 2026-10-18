<template>
  <ApiSessionGate v-if="error instanceof ApiError && error.status === 401" @signed-in="$emit('retry')" />
  <p v-if="error" class="feedback error" role="alert">{{ error instanceof Error ? error.message : '暂时无法读取账户数据。' }}</p>
  <p v-else-if="refreshing" class="feedback" role="status">{{ hasData ? '正在更新，当前显示最近一次有效数据。' : '正在读取账户数据…' }}</p>
</template>
<script setup lang="ts">
import ApiSessionGate from './ApiSessionGate.vue'
import { ApiError } from '../services/dataApi'
defineProps<{ error: unknown; refreshing?: boolean; hasData?: boolean }>()
defineEmits<{ retry: [] }>()
</script>
