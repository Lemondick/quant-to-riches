<template>
  <section aria-label="市场指数" class="index-region">
    <p class="subtle index-help">横向浏览全部指数 · 公开源延迟时长未知，各项以来源时间为准。</p>
    <div v-if="cards.length" ref="track" class="index-strip" role="list" tabindex="0" aria-label="指数卡片，左右方向键浏览，Home 和 End 跳到首尾" @keydown="scrollWithKeys">
      <article v-for="card in cards" :key="card.definition.id" class="stat-card index-card" :class="card.tone" :data-index-id="card.definition.id" role="listitem" tabindex="0" :aria-label="card.definition.name + '，' + card.qualityLabel + '，' + card.value">
        <div class="stat-top"><strong>{{ card.definition.name }}<small v-if="card.definition.product_status === 'provisional'" class="provisional">暂定</small></strong><span class="index-market">{{ markets[card.definition.market] ?? card.definition.market }} · {{ card.definition.currency }}</span></div>
        <div class="stat-value" :class="card.tone">{{ card.value }}</div>
        <div class="stat-foot" :class="card.tone"><span>涨跌 {{ card.change }}</span><span>{{ card.percent }}</span></div>
        <p class="index-quality" :class="{ caution: card.quality !== 'valid' }">{{ card.qualityLabel }}<span v-if="card.reason"> · {{ card.reason }}</span></p>
        <p class="index-source" :title="card.sourceLabel">{{ card.sourceLabel }}</p>
      </article>
    </div>
    <p v-else class="subtle" role="status">指数目录暂不可用，请刷新行情。</p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MarketSnapshot } from '../services/dataApi'
import { indexCards } from '../utils/indexPresentation'
const props = defineProps<{ snapshot: MarketSnapshot }>()
const cards = computed(() => indexCards(props.snapshot)), track = ref<HTMLElement>()
const markets: Record<string, string> = { CN: 'A股', HK: '港股', JP: '日本', US: '美股' }
function scrollWithKeys(event: KeyboardEvent) {
  const node = track.value
  if (!node || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  if (event.key === 'Home' || event.key === 'End') node.scrollLeft = event.key === 'Home' ? 0 : node.scrollWidth
  else node.scrollLeft += (event.key === 'ArrowLeft' ? -1 : 1) * (node.firstElementChild?.getBoundingClientRect().width ?? 280)
}
</script>
<style scoped>
.index-region{min-width:0;margin-bottom:14px}
.index-help{font-size:11px;margin:0 0 8px}
.index-strip{display:grid;grid-auto-flow:column;grid-auto-columns:calc((100% - 36px)/4);gap:12px;overflow-x:auto;overscroll-behavior-x:contain;scroll-snap-type:x mandatory;padding:3px 0 10px;scrollbar-width:thin;min-width:0}
.index-card.stat-card{height:auto;min-height:190px;min-width:0;scroll-snap-align:start;animation:none;transition:border-color .15s}
.index-card:hover{transform:none}
.index-card .stat-value{animation:none;font-size:26px;margin-top:12px}
.index-card .stat-top{gap:8px;align-items:flex-start;min-height:32px}
.index-card .stat-top strong{font-size:13px;line-height:1.5;color:var(--text-1)}
.index-market{font-size:10px;white-space:nowrap}
.provisional{font-size:10px;font-weight:400;margin-left:5px;color:var(--text-2)}
.index-quality,.index-source{font-size:10px;line-height:1.5;margin:8px 0 0;color:var(--text-3);overflow-wrap:anywhere;min-height:30px}
.index-quality.caution{color:var(--text-2)}
.index-source{margin-top:3px}
.neutral{color:var(--text-2)}
@media(min-width:1800px){.index-strip{grid-auto-columns:calc((100% - 48px)/5)}}
@media(min-width:2400px){.index-strip{grid-auto-columns:calc((100% - 60px)/6)}}
@media(max-width:900px){.index-strip{grid-auto-columns:calc((100% - 24px)/3)}}
@media(max-width:640px){.index-strip{grid-auto-columns:min(290px,86%)}.index-card.stat-card{padding:14px 16px}}
</style>
