<template>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!snapshot" @retry="refresh()" />
  <SentimentWidget :snapshot="snapshot ?? null" />
</template>
<script setup lang="ts">
import BackendState from './BackendState.vue'
import SentimentWidget from './SentimentWidget.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadSentiment } from '../services/batch5Api'

/**
 * 情绪小组件：调契约 GET api/v1/sentiment（后端缓存 60 分钟，QUANT_SENTIMENT_CACHE_MIN）。
 * 无数据时展示 warning 空态——铁律：不虚构情绪数字。
 */
const { data: snapshot, error, refreshing, refresh } = useAccountResource('sentiment-widget', loadSentiment)
</script>
