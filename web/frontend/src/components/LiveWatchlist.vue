<template>
  <PageHeader title="我的自选" subtitle="关注标的、估值变化与重要信号集中呈现。"><button class="btn" :disabled="refreshing" @click="refresh()">刷新数据</button><button class="btn primary" @click="editing = true"><Plus :size="15" />添加自选</button></PageHeader>
  <ApiSessionGate v-if="error instanceof ApiError && error.status === 401" @signed-in="refresh()" />
  <p v-if="error" class="feedback error" role="alert">{{ error instanceof Error ? error.message : '自选暂不可用。' }}</p>
  <p v-if="stale" class="feedback" role="status">当前显示最近一次有效数据，正在后台更新。</p>
  <p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p>
  <AppDialog v-model="editing" title="添加自选"><form v-if="data" class="panel card-pad stack-form" aria-label="添加账户自选" @submit.prevent="add"><label>名称<input v-model.trim="draft.name" class="field" maxlength="80" required /></label><label>代码<input v-model.trim="draft.symbol" class="field" pattern="\d{6}" required /></label><label>类型<select v-model="draft.asset_type" class="field"><option value="fund">基金</option><option value="stock">股票</option><option value="index">指数</option></select></label><button class="btn primary" :disabled="mutating" type="submit">保存自选</button></form><template #footer><button class="btn" @click="editing = false">取消</button></template></AppDialog>
  <div v-if="data" class="panel table-panel"><div class="panel-head"><div><h2>自选列表</h2><p>基金榜的关注操作会同步至此，支持按名称和代码查找。<span data-update-time> · {{ updateTime(oldestUpdate([...data.quotes.items, ...data.valuations.items])) }}</span></p></div><span class="data-badge">账户自选</span></div><FilterBar v-model="category" v-model:query="search" :options="['全部自选', '指数', '股票', '基金']" class="panel-filters" label="自选类型" search-label="筛选自选" /><DataTable table-id="live-watchlist" label="账户自选列表" :headers="['名称', '代码', '最新价', '涨跌幅', '今日成交额', '估值状态', 'AI信号', '操作']" :rows="rows" :numeric-columns="[2, 3, 4]" :min-width="900" empty-title="当前账户暂无自选">
    <template #cell-0="{ row, value }"><button v-if="isFund(String(row[1]))" class="text-action" @click="selected = String(row[1])">{{ value }}</button><span v-else>{{ value }}</span></template><template #cell-7="{ row }"><button class="text-action" :disabled="mutating" @click="remove(String(row[1]))">移除</button></template>
  </DataTable></div>
  <LiveFundDetail v-if="selected" :symbol="selected" />
</template>
<script setup lang="ts">
import { updateTime, oldestUpdate } from '../utils/dataQuality'

import ApiSessionGate from './ApiSessionGate.vue'
import { ApiError } from '../services/dataApi'

import { computed, ref, reactive, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Plus } from 'lucide-vue-next'
import AppDialog from './AppDialog.vue'
import FilterBar from './FilterBar.vue'
import PageHeader from './PageHeader.vue'
import DataTable from './DataTable.vue'
import LiveFundDetail from './LiveFundDetail.vue'
import { useDashboardSummary } from '../composables/useDashboardSummary'
const { data, error, refreshing, stale, refresh, session, mutate, mutating } = useDashboardSummary()
const selected = ref<string>(), category = ref('全部自选')
const isFund = (symbol: string) => data.value?.preferences.watchlist.some(item => item.symbol === symbol && item.asset_type === 'fund')
const route = useRoute(), search = ref(String(route.query.q ?? '')), editing = ref(false), actionError = ref('')
const draft = reactive({ name: '', symbol: '', asset_type: 'fund' })
watch(() => route.query.q, value => { search.value = String(value ?? '') })
watch(() => session.value?.cache_scope, () => { selected.value = undefined; editing.value = false; actionError.value = ''; Object.assign(draft, { name: '', symbol: '', asset_type: 'fund' }) })
async function add() { actionError.value = ''; try { await mutate('/preferences/watchlist', { items: [...(data.value?.preferences.watchlist ?? []), { ...draft }] }, 'PUT'); editing.value = false; Object.assign(draft, { name: '', symbol: '', asset_type: 'fund' }) } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '自选保存失败。' } }
async function remove(symbol: string) { actionError.value = ''; try { await mutate('/preferences/watchlist', { items: (data.value?.preferences.watchlist ?? []).filter(item => item.symbol !== symbol) }, 'PUT') } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '自选更新失败。' } }
const rows = computed(() => (data.value?.preferences.watchlist ?? []).filter(item => (category.value === '全部自选' || item.asset_type === ({ '指数': 'index', '股票': 'stock', '基金': 'fund' } as Record<string, string>)[category.value]) && (!search.value.trim() || `${item.name} ${item.symbol}`.toLowerCase().includes(search.value.trim().toLowerCase()))).map(item => {
  const quote = data.value?.quotes.items.find(quote => quote.symbol === item.symbol && quote.asset_type === item.asset_type)
  const valuation = item.asset_type === 'fund' ? data.value?.valuations.items.find(value => value.symbol === item.symbol) : undefined
  const rate = quote?.change_rate ?? valuation?.change_rate ?? valuation?.official_change_rate
  return [item.name, item.symbol, quote?.price ?? valuation?.estimate_nav ?? valuation?.official_nav ?? '—', rate == null ? '—' : rate + '%', '—', valuation?.valuation_type === 'latest_nav' ? '官方净值' : quote || valuation ? '公开快照' : '暂不可用', '未生成', item.symbol]
}))
</script>
