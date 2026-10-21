<template>
  <section class="panel table-panel fund-catalog" aria-label="基金分类目录">
    <div class="panel-head"><div><h2>基金分类目录</h2><p>按公开资料多维筛选；未核验资料保留未知，与日涨幅榜独立。</p></div><button class="btn" :disabled="refreshing" @click="refresh(true)">重新读取</button></div>
    <form class="catalog-search" role="search" @submit.prevent="search"><label>代码或名称<input v-model="draft" class="field" maxlength="100" placeholder="搜索基金名称或代码" /></label><button class="btn primary" type="submit">搜索目录</button><button class="btn" type="button" @click="clear">清空筛选</button></form>
    <div class="catalog-filters" aria-label="基金组合筛选">
      <details v-for="dimension in dimensions" :key="dimension.key"><summary>{{ dimension.label }}{{ filters[dimension.key]?.length ? `（${filters[dimension.key].length}）` : '' }}</summary><div class="catalog-options"><label v-for="option in categories?.dimensions[dimension.key] ?? []" :key="option.value"><input type="checkbox" :checked="filters[dimension.key]?.includes(option.value)" @change="toggle(dimension.key, option.value)" /><span>{{ option.label }} <small>{{ option.count }}</small></span></label><span v-if="!categories?.dimensions[dimension.key]?.length" class="subtle">暂无核验分类</span></div></details>
    </div>
    <BackendState :error="error || categoriesError" :refreshing="refreshing" :has-data="!!data" @retry="retry" />
    <template v-if="data">
      <p class="catalog-source" role="status">东方财富供应商目录 {{ data.directory_count }} 条 · {{ data.stale ? '目录已过期，保留最近有效快照' : '目录来源发布时间未知，无法确认延迟' }} · 抓取 {{ stamp(data.fetched_at) }}。净值日期单独展示；目录数量不代表官方全市场覆盖。</p>
      <p class="catalog-source">{{ data.classification_note }} 投资范围是明确披露范围，不代表当前持仓。份额类别缺少直接证据时显示待核验。</p>
      <DataTable table-id="live-fund-catalog" label="基金分类结果" :headers="['基金', '代码', '分类与标签', '正式单位净值', '净值日期', '公布日涨跌', '净值质量', '来源与抓取时间', '操作']" :rows="rows" :numeric-columns="[3, 5]" :sortable="false" :min-width="1320" empty-title="没有符合条件的基金" empty-text="调整搜索或分类组合后重试。">
        <template #summary>筛选 {{ data.total }} 条 · 目录 {{ data.directory_count }} 条</template>
        <template #cell-0="{ row, value }"><button class="table-name-button" @pointerenter="prepareFundDetail(String(row[1]))" @focus="prepareFundDetail(String(row[1]))" @click="$emit('select', String(row[1]))">{{ value }}</button></template>
        <template #cell-2="{ row }"><div class="catalog-tags"><span v-for="tag in tagsFor(String(row[1]))" :key="tag" class="tag blue">{{ tag }}</span></div><details class="catalog-evidence"><summary>分类依据</summary><p v-for="(proof, index) in fundFor(String(row[1]))?.classification_evidence ?? []" :key="index">{{ proof.value }} · {{ proof.source }} / {{ proof.source_field }} · {{ stamp(proof.fetched_at) }}<br />{{ proof.source_text }}</p><p v-if="!fundFor(String(row[1]))?.classification_evidence?.length">未取得可核验分类事实</p><p v-if="fundFor(String(row[1]))?.benchmark_text">业绩基准：{{ fundFor(String(row[1]))?.benchmark_text }}；关联只表示基准成分，不代表跟踪关系。</p></details></template>
        <template #cell-8="{ row }"><button class="text-action" :disabled="busy || !canFollow" :aria-label="(followed.includes(String(row[1])) ? '取消关注：' : '关注：') + row[0]" @click="$emit('follow', String(row[1]), String(row[0]))">{{ !canFollow ? '登录后关注' : followed.includes(String(row[1])) ? '已关注' : '关注' }}</button></template>
      </DataTable>
      <div class="catalog-pagination"><button class="btn" :disabled="page <= 1 || refreshing" @click="page--">上一页</button><span role="status">第 {{ page }} / {{ Math.max(1, Math.ceil(data.total / 50)) }} 页</span><button class="btn" :disabled="page * 50 >= data.total || refreshing" @click="page++">下一页</button></div>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import BackendState from './BackendState.vue'
import DataTable from './DataTable.vue'
import { useApiLoader } from '../composables/useApiLoader'
import { createDataApi } from '../services/dataApi'
import { catalogPath, catalogPercent, catalogStamp as stamp, validateCatalog, type CatalogFilters, type CatalogResult, type CatalogCategories, type CatalogFund } from '../services/fundCatalog'
import { prepareFundDetail } from '../services/fundData'
defineProps<{ followed: string[]; busy: boolean; canFollow: boolean }>()
defineEmits<{ select: [symbol: string]; follow: [symbol: string, name: string] }>()
const base = import.meta.env.VITE_API_BASE_URL ?? '', api = createDataApi(base), prefix = base + ':public:fund-catalog:'
const draft = ref(''), query = ref(''), page = ref(1), filters = reactive<CatalogFilters>({})
const path = computed(() => catalogPath(query.value, page.value, filters))
const { data, error, refreshing, refresh } = useApiLoader(computed(() => prefix + path.value), async (signal, key) => {
  const request = key.slice(prefix.length)
  return validateCatalog(await api.read<CatalogResult>(request, signal), request)
}, 60000, false, 60000)
const { data: categories, error: categoriesError, refresh: refreshCategories } = useApiLoader(base + ':public:fund-categories', async signal => {
  const value = await api.read<CatalogCategories>('/funds/categories', signal)
  if (!value.dimensions || typeof value.dimensions !== 'object') throw new Error('基金分类目录格式无效。')
  return value
}, 60000)
const dimensions = [{ key: 'asset_class', label: '资产类别' }, { key: 'fund_type', label: '供应商类型' }, { key: 'investment_scope', label: '投资范围' }, { key: 'strategy_tags', label: '策略标签（同时满足）' }, { key: 'product_structure', label: '产品结构' }, { key: 'benchmark_index_id', label: '关联业绩基准' }, { key: 'share_class', label: '份额类别' }, { key: 'regulatory_category', label: 'QDII制度类别' }, { key: 'classification_status', label: '核验状态' }]
function search() { page.value = 1; query.value = draft.value.trim() }
function clear() { draft.value = ''; query.value = ''; page.value = 1; for (const key of Object.keys(filters)) delete filters[key] }
function toggle(key: string, value: string) { const values = filters[key] ?? []; filters[key] = values.includes(value) ? values.filter(item => item !== value) : [...values, value]; page.value = 1 }
async function retry() { await Promise.allSettled([refresh(true), refreshCategories(true)]) }
const fundFor = (symbol: string) => data.value?.items.find(row => row.symbol === symbol)
function tagsFor(symbol: string) { const row = fundFor(symbol); return row ? [row.asset_class ?? '资产待核验', row.product_structure, ...row.investment_scope, ...row.strategy_tags, row.share_class ? row.share_class + '类' : '份额待核验', row.classification_is_stale ? '分类资料已过期' : ({ partial: '部分核验', conflict: '资料冲突', unknown: '待核验' })[row.classification_status as 'partial' | 'conflict' | 'unknown'] ?? '待核验'].filter((value): value is string => !!value) : [] }
function navStatus(row: CatalogFund) { return row.nav_reason === 'money_fund_metric_not_verified' ? '货币基金口径待核验' : ({ valid: '按公布日期核验', stale: '净值已过期', unknown_time: '时效待核验', unavailable: '尚无正式净值快照' })[row.nav_quality_status as 'valid' | 'stale' | 'unknown_time' | 'unavailable'] ?? '待核验' }
const rows = computed(() => (data.value?.items ?? []).map(row => [row.name, row.symbol, '', row.unit_nav ?? '—', row.nav_date ?? '—', catalogPercent(row.daily_return), navStatus(row), `${row.nav_source ?? '暂无净值来源'} · 净值抓取 ${stamp(row.nav_fetched_at)}；目录 ${row.data_source ?? data.value?.data_source} · ${stamp(row.fetched_at ?? data.value?.fetched_at)}`, row.symbol]))
</script>
<style scoped>
.fund-catalog { min-width: 0; }
.catalog-search { display: flex; flex-wrap: wrap; align-items: end; gap: 10px; padding: 0 20px 16px; }
.catalog-search label { display: grid; gap: 7px; flex: 1 1 220px; min-width: 0; max-width: 480px; font-size: 12px; color: var(--text-3); }
.catalog-search input { width: 100%; min-width: 0; box-sizing: border-box; }
.catalog-filters { display: grid; grid-template-columns: repeat(auto-fit,minmax(160px,1fr)); gap: 8px; padding: 0 20px 12px; }
.catalog-filters details { min-width: 0; border: 1px solid var(--border); border-radius: 7px; padding: 10px; font-size: 12px; }
.catalog-filters summary { cursor: pointer; overflow-wrap: anywhere; }
.catalog-options { display: grid; gap: 10px; max-height: 190px; overflow-y: auto; padding-top: 12px; }
.catalog-options label { display: flex; gap: 7px; align-items: start; overflow-wrap: anywhere; }
.catalog-options input { width: auto; flex: 0 0 auto; }
.catalog-source { margin: 0; padding: 8px 20px; color: var(--text-3); font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.catalog-tags { display: flex; flex-wrap: wrap; gap: 5px; max-width: 250px; }
.catalog-evidence { margin-top: 8px; max-width: 250px; font-size: 11px; white-space: normal; overflow-wrap: anywhere; color: var(--text-3); }
.catalog-evidence p { line-height: 1.7; }
.catalog-pagination { display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 12px; padding: 16px; font-size: 12px; }
@media (max-width: 420px) { .catalog-search, .catalog-filters { padding-left: 12px; padding-right: 12px; } .catalog-filters { grid-template-columns: repeat(2,minmax(0,1fr)); } .catalog-filters details { padding: 8px; font-size: 11px; line-height: 1.5; } }
</style>
