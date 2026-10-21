<template>
  <SentimentWidget :snapshot="snapshot" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import SentimentWidget from './SentimentWidget.vue'
import { useAccountResource } from '../composables/useAccountResource'
import type { SentimentSnapshot } from '../services/batch5Api'

/**
 * 情绪小组件：调契约 GET api/v1/sentiment（后端缓存 60 分钟，QUANT_SENTIMENT_CACHE_MIN）。
 * 无数据时展示 warning 空态——铁律：不虚构情绪数字。
 */
const { session, read } = useAccountResource('sentiment-widget',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const snapshot = ref<SentimentSnapshot | null>(null)

async function load() {
  try {
    snapshot.value = await read<SentimentSnapshot>('/sentiment')
  } catch {
    snapshot.value = { items: [], warning: '后端情绪接口尚未就绪（A 并行开发中），待联调。' }
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
