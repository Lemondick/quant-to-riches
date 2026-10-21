<template>
  <PageHeader title="虚拟滚动性能验证" subtitle="10000 行账本数据的虚拟滚动实验室（内部页面，未加入导航）。" />
  <div class="page-context"><span class="data-badge">性能实验室</span><span>仅验证渲染行数正确，不对帧率做硬性断言；单元测试见 tests/virtual-scroll.test.mjs。</span></div>
  <div class="kpi-grid">
    <div class="panel metric-card"><span class="metric-label">总行数</span><div class="metric-value">{{ rows.length.toLocaleString('zh-CN') }}</div><div class="metric-note">mock 账本数据</div></div>
    <div class="panel metric-card"><span class="metric-label">滚动帧率（实时）</span><div class="metric-value">{{ fps }} <small>fps</small></div><div class="metric-note">rAF 采样，滚动时观察</div></div>
    <div class="panel metric-card"><span class="metric-label">渲染模式</span><div class="metric-value">{{ isVirtual ? '虚拟滚动' : '普通表格' }}</div><div class="metric-note">阈值 {{ threshold }} 行自动切换</div></div>
  </div>
  <div class="inline-actions section-space">
    <button class="btn" @click="scrollTo(0)">滚动到顶部</button>
    <button class="btn" @click="scrollTo(0.5)">滚动到中部</button>
    <button class="btn" @click="scrollTo(1)">滚动到底部</button>
    <span class="subtle">观察帧率与底部「虚拟滚动渲染中 N 行」计数</span>
  </div>
  <div ref="labRef" class="panel table-panel section-space">
    <DataTable table-id="lab-ledger" label="万行账本" :headers="headers" :rows="rows" :numeric-columns="[4, 5, 6, 7, 8]"
      :min-width="1100" virtual :virtual-threshold="threshold" :cell-html="ledgerTypeTagHtml" empty-title="无数据">
      <template #summary>共 <b>{{ rows.length.toLocaleString('zh-CN') }}</b> 条记录 · 虚拟滚动渲染可视窗口</template>
    </DataTable>
  </div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import { mockLedgerRows } from '../data/batch2'
import { ledgerTypeTagHtml } from '../utils/table'

const headers = ['名称', '时间', '代码', '方向', '成交金额', '成交价格', '数量', '手续费', '已实现收益', '备注']
const threshold = 300
const rows = ref(mockLedgerRows(10000))
const isVirtual = ref(rows.value.length > threshold)
const fps = ref(0)
const labRef = ref<HTMLElement>()

let raf = 0, frames = 0, last = 0
function loop(now: number) {
  frames++
  if (!last) last = now
  if (now - last >= 500) {
    fps.value = Math.round((frames * 1000) / (now - last))
    frames = 0
    last = now
  }
  raf = requestAnimationFrame(loop)
}
onMounted(() => { raf = requestAnimationFrame(loop) })
onBeforeUnmount(() => cancelAnimationFrame(raf))

function scrollTo(fraction: number) {
  const scroller = labRef.value?.querySelector('.virtual-tbody') as HTMLElement | null
  if (!scroller) return
  scroller.scrollTo({ top: fraction * scroller.scrollHeight, behavior: fraction === 0 ? 'auto' : 'smooth' })
}
</script>
<style scoped>
.inline-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.subtle{font-size:11px;color:var(--text-2)}
.metric-value small{font-size:12px;font-weight:400;color:var(--text-2)}
</style>
