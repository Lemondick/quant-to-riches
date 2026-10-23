<template>
  <div v-if="task || requesting || error || notice" class="panel card-pad section-space" aria-label="计算任务状态">
    <p role="status">{{ task ? labels[task.status] : requesting ? '正在查询任务' : '计算暂不可用' }}</p>
    <p v-if="task && ['queued', 'running'].includes(task.status)" class="subtle">计算在后台继续，刷新或重新打开本页可恢复查看。</p>
    <p v-if="error" class="feedback error" role="alert">{{ error instanceof Error ? error.message : '任务查询失败，请重试。' }}</p>
    <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
    <div class="inline-actions">
      <button v-if="task && ['queued', 'running', 'cancel_requested'].includes(task.status)" class="btn" :disabled="cancelling" @click="$emit('cancel')">{{ cancelling ? '正在取消' : '取消任务' }}</button>
      <button v-if="error && recoverable" class="btn" :disabled="requesting" @click="$emit('refresh')">继续查询</button>
    </div>
  </div>
</template>
<script setup lang="ts">
import type { ComputeTask } from '../composables/useComputeTask'
defineProps<{ task: ComputeTask | null; error?: unknown; notice?: string; requesting: boolean; cancelling: boolean; recoverable: boolean }>()
defineEmits<{ cancel: []; refresh: [] }>()
const labels = { queued: '排队中', running: '计算中', cancel_requested: '取消中', completed: '已完成', failed: '计算失败', cancelled: '已取消', interrupted: '已中断' }
</script>
