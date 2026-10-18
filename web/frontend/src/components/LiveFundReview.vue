<template>
  <PageHeader title="基金复核" subtitle="把表现、关注状态与研究记录放在同一处。"><button class="btn" :disabled="refreshing" @click="refresh()">刷新清单</button><RouterLink class="btn primary" to="/watchlist">添加基金</RouterLink></PageHeader>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <template v-if="data"><div class="page-context"><span class="data-badge">账户复核清单</span><span>公开净值保留原始来源与公告日期。</span></div><div class="kpi-grid"><div class="panel metric-card"><span class="metric-label">复核清单</span><div class="metric-value">{{ allRows.length }} 只</div><div class="metric-note">当前持仓与自选基金</div></div><div class="panel metric-card"><span class="metric-label">净值已就绪</span><div class="metric-value up">{{ allRows.filter(row => row[2] !== '—').length }} 只</div><div class="metric-note">最近有效公开净值</div></div><div class="panel metric-card"><span class="metric-label">待补充资料</span><div class="metric-value">{{ allRows.filter(row => row[2] === '—').length }} 只</div><div class="metric-note">尚无有效净值观测</div></div></div></template>
  <div v-if="data" class="panel table-panel section-space"><div class="panel-head"><div><h2>基金复核清单</h2><p>点击基金名称，查看下方走势与研究资料。<span data-update-time> · {{ updateTime(oldestUpdate(data.valuations.items)) }}</span></p></div></div><FilterBar v-model="status" v-model:query="query" :options="['全部', '净值已就绪', '待补充资料']" class="panel-filters" label="复核状态" placeholder="搜索基金名称或代码" search-label="搜索复核基金" /><DataTable table-id="native-fund-review" label="账户基金复核" :headers="['名称', '代码', '最新估值 / 净值', '净值日期', '来源']" :rows="rows" :numeric-columns="[2]" empty-title="当前账户暂无基金"><template #cell-0="{ row, value }"><button class="text-action" @click="selected = String(row[1])">{{ value }}</button></template></DataTable></div>
  <LiveFundDetail v-if="selected" :symbol="selected" />
</template>
<script setup lang="ts">
import { updateTime, oldestUpdate } from '../utils/dataQuality'

import { computed, ref, watch } from 'vue'
import FilterBar from './FilterBar.vue'
import PageHeader from './PageHeader.vue'
import DataTable from './DataTable.vue'
import BackendState from './BackendState.vue'
import LiveFundDetail from './LiveFundDetail.vue'
import { useDashboardSummary } from '../composables/useDashboardSummary'
const { data, error, refreshing, session, refresh } = useDashboardSummary()
const selected = ref<string>(), status = ref('全部'), query = ref('')
watch(() => session.value?.cache_scope, () => { selected.value = undefined })
const allRows = computed(() => {
  const funds = new Map<string, string>()
  for (const item of [...(data.value?.portfolio.positions ?? []), ...(data.value?.preferences.watchlist ?? [])]) if (item.asset_type === 'fund') funds.set(item.symbol, item.name)
  return [...funds].map(([symbol, name]) => { const value = data.value?.valuations.items.find(item => item.symbol === symbol); return [name, symbol, value?.estimate_nav ?? value?.official_nav ?? '—', value?.nav_date ?? '—', value?.source ?? '暂不可用'] })
})
const rows = computed(() => allRows.value.filter(row => (String(row[0]) + row[1]).includes(query.value.trim()) && (status.value === '全部' || (status.value === '净值已就绪' ? row[2] !== '—' : row[2] === '—'))))
</script>
