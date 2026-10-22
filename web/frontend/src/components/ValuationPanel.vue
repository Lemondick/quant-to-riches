<template>
  <PageHeader title="估值仪表盘" subtitle="六大指数 PE/PB 历史百分位、ERP 与市场温度计。" />
  <div class="page-context"><span class="data-badge">{{ demo ? '演示数据' : '公开行情' }}</span><span>百分位基于近 10 年历史 · 截至 {{ snapshot.as_of }}</span></div>
  <p v-for="warning in snapshot.warnings ?? []" :key="warning" class="feedback" role="status">{{ warning }}</p>
  <div class="two-col">
    <div class="panel card-pad gauge-panel">
      <div class="card-title">市场温度计</div>
      <TemperatureGauge v-if="snapshot.temperature.score !== null" :score="snapshot.temperature.score" :label="snapshot.temperature.label" :size="240" />
      <p v-else class="feedback" role="status">{{ snapshot.temperature.label || '数据不足，暂无市场温度。' }}</p>
      <p class="subtle">0–100：越低越便宜。当前所处分位综合六大指数 PE/PB 百分位。</p>
    </div>
    <div class="panel card-pad">
      <div class="card-title">最低估指数</div>
      <div v-if="cheapest" class="cheapest">
        <b>{{ cheapest.name }}</b>
        <span class="tag green">PE {{ cheapest.pe_pct }}% · PB {{ cheapest.pb_pct }}%</span>
      </div>
      <p v-else class="subtle">暂无数据。</p>
      <div class="row-list">
        <div class="row"><span>温度分值</span><b>{{ snapshot.temperature.score ?? '—' }}</b></div>
        <div class="row"><span>温度判断</span><b>{{ snapshot.temperature.label || (snapshot.temperature.score === null ? '数据不足' : temperatureLabel(snapshot.temperature.score)) }}</b></div>
        <div class="row"><span>覆盖指数</span><b>{{ snapshot.items.length }} 个</b></div>
      </div>
      <RouterLink class="text-action section-space" to="/">回首页查看紧凑小组件 <ArrowUpRight :size="13" aria-hidden="true" /></RouterLink>
    </div>
  </div>
  <div class="panel table-panel section-space">
    <div class="panel-head"><div><h2>指数估值百分位</h2><p>百分位越低越便宜；ERP 为股权风险溢价（%）。</p></div></div>
    <div class="card-pad">
      <div v-for="item in orderedItems" :key="item.symbol" class="index-block">
        <div class="index-head"><b>{{ item.name }}</b><span class="mono">{{ item.symbol }}</span>
          <span class="subtle">PE {{ item.pe ?? '—' }} · PB {{ item.pb ?? '—' }} · ERP {{ item.erp === null ? '—' : item.erp + '%' }}</span>
        </div>
        <div v-for="metric in [{ key: 'pe', label: 'PE 百分位', pct: item.pe_pct }, { key: 'pb', label: 'PB 百分位', pct: item.pb_pct }]" :key="metric.key" class="pct-row">
          <span class="pct-label">{{ metric.label }}</span>
          <div class="pct-track"><div class="pct-fill" :class="percentileTone(metric.pct)" :style="{ width: metric.pct + '%' }"></div>
            <span class="pct-marker" :style="{ left: metric.pct + '%' }"></span></div>
          <b class="pct-value" :class="percentileTone(metric.pct)">{{ metric.pct }}%</b>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { ArrowUpRight } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import TemperatureGauge from './TemperatureGauge.vue'
import { cheapestIndex, percentileTone, temperatureLabel, VALUATION_INDEX_ORDER, type ValuationSnapshot } from '../services/batch2Api'

const props = withDefaults(defineProps<{ snapshot: ValuationSnapshot; demo?: boolean }>(), { demo: false })
const order = new Map(VALUATION_INDEX_ORDER.map((symbol, index) => [symbol, index]))
const orderedItems = computed(() => [...props.snapshot.items].sort((a, b) => (order.get(a.symbol) ?? 99) - (order.get(b.symbol) ?? 99)))
const cheapest = computed(() => cheapestIndex(props.snapshot))
</script>
<style scoped>
.card-title{font-size:14px;font-weight:700;margin-bottom:8px}
.subtle{font-size:11px;color:var(--text-2);margin:4px 0}
.mono{font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums}
.gauge-panel{text-align:center}
.cheapest{display:flex;align-items:center;gap:10px;font-size:16px;margin:8px 0}
.tag.green{background:rgba(24,60,51,.7);color:#3ddc84}
.index-block{padding:14px 0;border-bottom:1px solid rgba(34,51,77,.5)}
.index-block:last-child{border-bottom:0}
.index-head{display:flex;align-items:baseline;gap:10px;margin-bottom:8px;flex-wrap:wrap}
.pct-row{display:grid;grid-template-columns:76px 1fr 52px;align-items:center;gap:10px;margin:6px 0}
.pct-label{font-size:11px;color:var(--text-2)}
.pct-track{position:relative;height:10px;border-radius:5px;background:rgba(34,51,77,.6);overflow:visible}
.pct-fill{height:100%;border-radius:5px}
.pct-fill.low{background:linear-gradient(90deg,#1f7a4d,#3ddc84)}
.pct-fill.mid{background:linear-gradient(90deg,#2f6fe0,#4d8dff)}
.pct-fill.high{background:linear-gradient(90deg,#c2543a,#e05d5d)}
.pct-marker{position:absolute;top:-3px;bottom:-3px;width:2px;background:#fff;opacity:.85;border-radius:1px}
.pct-value{font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
.pct-value.low{color:#3ddc84}.pct-value.mid{color:#6ca0ff}.pct-value.high{color:#ff6d7d}
</style>
