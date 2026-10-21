<template>
  <div class="panel sentiment-panel">
    <div class="panel-head">
      <div><h2>市场情绪</h2><p>融资余额、成交额、换手率、涨跌家数比<span v-if="latestSource"> · {{ latestSource }}</span></p></div>
      <span v-if="!emptyReason" class="tag blue">情绪</span>
    </div>
    <div v-if="emptyReason" class="card-pad">
      <p class="feedback" role="status"><AlertTriangle :size="15" aria-hidden="true" />{{ emptyReason }}</p>
      <p class="subtle">情绪指标依赖合规公开数据源；数据源未接入时本组件不展示任何数字，接入后自动显示。</p>
    </div>
    <div v-else class="card-pad">
      <div class="sentiment-grid">
        <div v-for="item in orderedItems" :key="item.name" class="sentiment-metric">
          <span class="subtle">{{ item.name }}</span>
          <b>{{ item.value }}<em>{{ item.unit }}</em></b>
          <span class="sentiment-meta">更新于 {{ item.updated_at.slice(0, 16).replace('T', ' ') }} · {{ item.source }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'
import { SENTIMENT_METRIC_ORDER, sentimentEmptyReason, type SentimentSnapshot } from '../services/batch5Api'

const props = defineProps<{ snapshot: SentimentSnapshot | null }>()
const emptyReason = computed(() => sentimentEmptyReason(props.snapshot))
const orderedItems = computed(() => {
  const rank = new Map(SENTIMENT_METRIC_ORDER.map((name, index) => [name, index]))
  return [...(props.snapshot?.items ?? [])].sort((a, b) => (rank.get(a.name) ?? 99) - (rank.get(b.name) ?? 99))
})
const latestSource = computed(() => {
  const items = props.snapshot?.items ?? []
  return items.length ? items.map(item => item.updated_at).sort().at(-1)?.slice(0, 16).replace('T', ' ') : null
})
</script>
<style scoped>
.sentiment-panel{margin-top:12px}
.sentiment-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.sentiment-metric{display:flex;flex-direction:column;gap:4px;padding:12px;border:1px solid rgba(42,64,94,.5);border-radius:8px;background:rgba(16,31,52,.5)}
.sentiment-metric b{font-size:20px}
.sentiment-metric b em{font-style:normal;font-size:12px;font-weight:400;color:#8fa3c4;margin-left:4px}
.sentiment-meta{font-size:11px;color:#8fa3c4}
.feedback svg{vertical-align:-2px;margin-right:6px}
</style>
