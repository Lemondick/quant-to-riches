<template>
  <LiveWatchlist v-if="liveBackend" />
  <template v-else>
  <PageHeader title="我的自选" subtitle="关注标的、估值变化与重要信号集中呈现。">
    <button class="btn" :disabled="category === '全部自选' && !query" @click="resetFilters"><RotateCcw :size="15" aria-hidden="true" />重置筛选</button>
    <RouterLink class="btn primary" to="/market?tab=funds"><Plus :size="15" aria-hidden="true" />添加自选</RouterLink>
  </PageHeader>
  <div class="panel table-panel">
    <div class="panel-head"><div><h2>自选列表</h2><p>基金榜的关注操作会同步至此，支持按名称和代码查找。</p></div><span class="data-badge">演示数据</span></div>
    <FilterBar v-model="category" v-model:query="query" :options="['全部自选', '指数', '股票', '基金']" class="panel-filters" label="自选类型" />
    <DataTable table-id="watchlist" label="自选列表" :headers="['名称', '代码', '最新价', '涨跌幅', '今日成交额', '估值状态', 'AI信号', '操作']" :rows="rows" :numeric-columns="[2, 3, 4]" :min-width="900" empty-title="暂无匹配的自选标的" empty-text="可调整筛选，或前往基金榜关注感兴趣的样本。">
      <template #summary>显示 <b>{{ rows.length }}</b> / {{ records.length }} 个标的</template>
      <template #cell-5="{ value }"><span :class="['tag', value === '低估' || value === '偏低' ? '' : 'blue']">{{ value }}</span></template>
      <template #cell-6="{ value }"><span class="signal-label" :class="{ up: value === '偏多' }">{{ value }}</span></template>
      <template #cell-7="{ row }"><button v-if="isSaved(String(row[1]))" class="text-action" :aria-label="'取消关注：' + row[0]" @click="toggleSave(String(row[1]))">取消关注</button><span v-else class="subtle">示例标的</span></template>
      <template #empty-action><button v-if="category !== '全部自选' || query" class="btn" @click="resetFilters">清除筛选</button><RouterLink v-else class="btn" to="/market?tab=funds">前往基金榜</RouterLink></template>
    </DataTable>
  </div>
  </template>
</template>

<script setup lang="ts">
import LiveWatchlist from '../components/LiveWatchlist.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Plus, RotateCcw } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import FilterBar from '../components/FilterBar.vue'
import { fundList } from '../data/funds'
import { useFollowedFunds } from '../composables/useFollowedFunds'

const category = ref('全部自选')
const query = ref('')
const route = useRoute()
watch(() => route.query.q, value => { query.value = typeof value === 'string' ? value : '' }, { immediate: true })
const { savedFunds, isSaved, toggleSave } = useFollowedFunds()
const examples = [
  { name: '沪深300', code: '000300', type: '指数', price: '3,733.85', delta: '+0.71%', volume: '¥ 3,824亿', valuation: '合理', signal: '偏多' },
  { name: '国泰房地产C', code: '015042', type: '基金', price: '1.084', delta: '+1.18%', volume: '—', valuation: '低估', signal: '关注' },
  { name: '中证500', code: '000905', type: '指数', price: '5,982.12', delta: '-0.34%', volume: '¥ 2,168亿', valuation: '合理', signal: '中性' },
  { name: '新能源ETF', code: '516160', type: '基金', price: '0.832', delta: '+2.41%', volume: '¥ 18.4亿', valuation: '偏低', signal: '偏多' }
]
const records = computed(() => [...examples, ...fundList.filter(fund => savedFunds.value.includes(fund.code)).map(fund => ({
  name: fund.name, code: fund.code, type: '基金', price: fund.nav, delta: fund.dayChange, volume: '—', valuation: '—', signal: '待研究'
}))])
const rows = computed(() => {
  const search = query.value.trim().toLowerCase()
  return records.value.filter(record =>
    (category.value === '全部自选' || record.type === category.value) &&
    (!search || record.name.toLowerCase().includes(search) || record.code.includes(search))
  ).map(record => [record.name, record.code, record.price, record.delta, record.volume, record.valuation, record.signal, record.code])
})
function resetFilters() { category.value = '全部自选'; query.value = '' }
</script>
