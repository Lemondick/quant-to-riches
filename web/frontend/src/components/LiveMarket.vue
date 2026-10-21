<template>
  <div class="market-page">
    <PageHeader title="市场行情" subtitle="指数走势、基金净值与行业、概念、地域板块。"><span class="data-badge">公开行情</span><button class="btn" :disabled="refreshing" @click="refreshMarket()"><RefreshCw :size="15" :class="{ 'spin-anim': refreshing }" />刷新行情</button></PageHeader>
    <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refreshMarket()" />
    <div class="market-nav-tabs" role="group" aria-label="行情分类"><button v-for="tab in tabs" :key="tab.id" type="button" class="market-tab-btn" :class="{ active: currentTab === tab.id }" :aria-pressed="currentTab === tab.id" @click="currentTab = tab.id">{{ tab.label }}</button></div>
    <div v-if="currentTab === 'funds'" class="fund-context-line" role="group" aria-label="基金浏览方式"><button class="btn" :class="{ primary: fundView === 'ranking' }" :aria-pressed="fundView === 'ranking'" @click="fundView = 'ranking'">日涨幅榜</button><button class="btn" :class="{ primary: fundView === 'catalog' }" :aria-pressed="fundView === 'catalog'" @click="fundView = 'catalog'">分类目录</button></div>
    <FundCatalog v-if="currentTab === 'funds' && fundView === 'catalog'" :followed="account.data.value?.preferences.watchlist.map(item => item.symbol) ?? []" :busy="account.mutating.value" :can-follow="!!account.session.value" @select="selected = $event" @follow="toggleFollow" />
    <template v-if="data && !(currentTab === 'funds' && fundView === 'catalog')">
      <p class="market-observation" role="status">{{ sessionLabel }} · 页面每 15 秒检查更新，各市场按本地时段采集（后台间隔 {{ data.refresh_interval_seconds ?? 30 }} 秒）；公开源可能延迟，以各项来源时间为准。</p>
      <div v-if="currentTab === 'index'" class="tab-content-fade">
        <IndexQuoteStrip :snapshot="data" />
        <div class="two-col">
          <IndexHistory :definitions="data.index_registry ?? []" :registry-version="data.registry_version" />
          <div class="market-side-panels">
            <div class="panel card-pad market-breadth-card">
              <div class="panel-header-inline"><div><div class="card-title">市场情绪与盘面</div><p class="subtle">两市上涨 / 下跌家数比<span data-update-time> · {{ updateTime(breadth?.updated_at) }}</span></p></div><span class="tag blue">{{ breadth ? (breadth.stale ? '最近有效统计' : breadth.unavailable.length ? '部分数据' : '最新交易日统计') : '暂无广度数据' }}</span></div>
              <div class="breadth-ratio-bar" role="img" :aria-label="breadth ? `上涨 ${breadth.advancing} 家，下跌 ${breadth.declining} 家，平盘 ${breadth.unchanged} 家` : '暂未取得有效盘面统计'">
                <template v-if="breadth && breadthTotal > 0"><div class="breadth-fill up" :style="{ width: breadth.advancing / breadthTotal * 100 + '%' }">上涨 {{ number(breadth.advancing, 0) }}</div><div class="breadth-fill down" :style="{ width: breadth.declining / breadthTotal * 100 + '%' }">下跌 {{ number(breadth.declining, 0) }}</div></template>
                <div v-else class="breadth-fill breadth-empty">{{ breadth ? '上涨 0 / 下跌 0' : '暂未取得有效盘面统计' }}</div>
              </div>
              <div class="breadth-stats-row"><div v-for="item in breadthStats" :key="item.label" class="breadth-stat" :title="item.title"><span class="subtle">{{ item.label }}</span><b>{{ item.value }}</b></div></div>
              <p v-if="breadth" class="breadth-observation"><span>统计 {{ breadth.observed_at.slice(0, 16).replace('T', ' ') }} · 平盘 {{ number(breadth.unchanged, 0) }}</span><a href="https://legulegu.com/stockdata/market-activity" target="_blank" rel="noopener noreferrer">公开统计 ↗</a></p>
            </div>
            <div class="panel card-pad market-industry-card"><div class="panel-header-inline"><div><div class="card-title">行业涨跌速览</div><p class="subtle">{{ sourceLabel(previewSource) }} · 行业分类<span data-update-time> · {{ updateTime(data.sector_catalogs?.find(item => item.source === previewSource && item.category === 'industry')?.updated_at) }}</span></p></div><button class="text-link-btn" @click="currentTab = 'sectors'">查看全部板块 →</button></div><div class="row-list"><div v-for="item in industryPreview" :key="sectorIdentity(item)" class="row"><span class="industry-name">{{ item.name }}</span><b :class="sectorTone(item)">{{ sectorValue(item) == null ? '—' : percent(sectorValue(item)!) }}</b></div></div></div>
          </div>
        </div>
        <div class="panel table-panel"><div class="panel-head"><div><h2>主要指数</h2><p><span data-update-time>{{ updateTime(oldestUpdate(data.indices)) }}</span></p></div><button class="btn ghost" @click="currentTab = 'funds'">切换至基金榜单</button></div><DataTable table-id="live-market-indices" label="主要指数" :headers="['指数', '代码', '点位', '涨跌幅', '观测时间']" :rows="indexCards(data).map(card => [card.definition.name + (card.definition.product_status === 'provisional' ? '（暂定）' : ''), card.definition.exchange_code, card.value, card.percent, card.sourceLabel + ' · ' + card.qualityLabel])" :numeric-columns="[2, 3]" empty-title="暂无可用指数快照" /></div>
      </div>
      <div v-else-if="currentTab === 'funds'" class="tab-content-fade">
        <div class="fund-context-line"><span class="data-badge">指数型基金日涨幅前十</span><span>{{ data.fund_rankings.length }} 只基金 · 净值日期以原始公告为准</span></div>
        <div class="fund-kpi-grid"><div v-for="(item, index) in fundHighlights" :key="index" class="panel fund-kpi-card" :class="{ 'gold-border': index === 0 }"><div class="kpi-top"><span class="kpi-tag">{{ ['本期领涨', '最新净值', '榜单数量', '净值日期'][index] }}</span><span class="subtle">最近有效快照</span></div><div class="kpi-main-name">{{ item.title }}</div><div class="metric-value">{{ item.value }}</div></div></div>
        <div class="panel table-panel section-space"><div class="panel-head"><div><h2>指数型基金日涨幅前十</h2><p>东方财富指数型基金榜单；公布净值非盘中实时估值，点击名称查看详情。<span data-update-time> · {{ updateTime(oldestUpdate(data.fund_rankings)) }}</span></p></div></div><FilterBar v-model="category" v-model:query="query" :options="['全部', '上涨', '下跌']" class="panel-filters" label="基金表现" placeholder="搜索基金名称或代码" search-label="搜索基金名称或代码" /><DataTable table-id="live-market-funds" label="基金排行" :headers="['基金', '代码', '单位净值', '涨跌幅', '净值日期', '操作']" :rows="fundRows" :numeric-columns="[2, 3]" :min-width="800" empty-title="暂无可用基金排行"><template #cell-0="{ row, value }"><button class="table-name-button" @pointerenter="prepareFundDetail(String(row[1]))" @focus="prepareFundDetail(String(row[1]))" @click="selected = String(row[1])">{{ value }}</button></template><template #cell-5="{ row }"><button class="text-action" :disabled="account.mutating.value" :aria-label="(isFollowed(String(row[1])) ? '取消关注：' : '关注：') + row[0]" @click="toggleFollow(String(row[1]), String(row[0]))">{{ isFollowed(String(row[1])) ? '已关注' : '关注' }}</button></template></DataTable></div>
      </div>
      <div v-else class="tab-content-fade">
        <div class="sector-kpi-grid"><div v-for="kind in sectorKinds" :key="kind.id" class="panel metric-card"><span class="metric-label">{{ kind.label }}</span><div class="metric-value">{{ catalogFor(kind.id)?.received ?? '—' }} <small>个</small></div><div class="metric-note">{{ catalogFor(kind.id)?.received ? (catalogFor(kind.id)?.catalog_stale ? '旧目录快照 · 总数未知' : catalogFor(kind.id)?.stale ? '报价更新失败 · 总数未知' : catalogFor(kind.id)?.complete === true ? '目录覆盖已核验' : '已接收条目 · 来源总数未知') : '目录暂不可用' }}</div></div></div>
        <div class="panel table-panel section-space">
          <div class="panel-head"><div><h2>全部板块</h2><p>按数据源分类展示已接收条目，覆盖状态见下方；涨跌幅单位为百分比。<span data-update-time> · {{ updateTime(activeCatalog?.updated_at) }}</span></p></div><label class="sector-source-label">数据源<select v-model="sectorSource" class="btn" aria-label="板块数据源"><option v-for="source in sectorSources" :key="source" :value="source">{{ sourceLabel(source) }}</option></select></label></div>
          <p class="sector-source-note">{{ sectorSource === 'sina' ? '新浪分类；涨跌幅为源提供的板块平均涨跌，与东方财富板块指数口径不同。该接口未提供行情时间，无法确认延迟。' : sectorSource === 'eastmoney' ? '东方财富分类，行业包含一、二、三级；涨跌幅与行情时间直接取自板块指数原始记录。' : '该快照未提供板块来源信息，请刷新页面以重新获取。' }}</p>
          <FilterBar v-model="sectorCategory" v-model:query="sectorQuery" :options="['行业', '概念', '地域']" class="panel-filters" label="板块分类" placeholder="搜索全部板块名称或代码" search-label="搜索全部板块名称或代码" />
          <p class="sector-source-note" role="status">{{ sectorStatus }} · 抓取 {{ displayTime(activeCatalog?.fetched_at) }} · 行情 {{ sectorSourceTime(activeCatalog ?? {}) ? sectorTimeLabel(activeCatalog ?? {}) : '来源未提供' }} <a :href="sectorSource === 'sina' ? 'https://finance.sina.com.cn/stock/sl/' : 'https://quote.eastmoney.com/center/boardlist.html'" target="_blank" rel="noopener noreferrer">核对来源 ↗</a></p>
          <DataTable class="sector-data-table" :key="sectorSource + sectorCategory + sectorQuery" table-id="live-market-full-sectors" label="全部板块" :headers="['板块', '代码', '涨跌幅', '成交额', '领涨股', '行情时间', '报价质量']" :rows="sectorRows" :numeric-columns="[2, 3]" :page-size="50" :min-width="960" empty-title="暂无可用板块记录" />
        </div>
      </div>
      <p v-if="data.stale_sections?.length" class="feedback">部分资料更新失败，保留原始观测：{{ data.stale_sections.map(section => ({ indices: '指数', fund_rankings: '基金榜', sector_rankings: '板块' })[section as 'indices' | 'fund_rankings' | 'sector_rankings'] ?? section).join('、') }}</p>
      <p v-if="data.stale || stale" class="feedback" role="status">当前显示最近一次有效快照。</p><p v-if="data.unavailable.length" class="feedback">暂不可用：{{ data.unavailable.join('、') }}</p>
    </template>
    <p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p>
    <LiveFundDetail v-if="selected" :symbol="selected" />
  </div>
</template>
<script setup lang="ts">
import { updateTime, oldestUpdate } from '../utils/dataQuality'

import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import DataTable from './DataTable.vue'
import IndexQuoteStrip from './IndexQuoteStrip.vue'
import { indexCards } from '../utils/indexPresentation'
import FilterBar from './FilterBar.vue'
import BackendState from './BackendState.vue'
import LiveFundDetail from './LiveFundDetail.vue'
import { useApiLoader } from '../composables/useApiLoader'
import { useDashboardSummary } from '../composables/useDashboardSummary'
import { createDataApi, type MarketSnapshot } from '../services/dataApi'
import { number, percent } from '../utils/format'
import IndexHistory from './IndexHistory.vue'
import { prepareFundDetail } from '../services/fundData'
import { sectorAmount, sectorCatalogStatus, sectorIdentity, sectorQuality, sectorSourceTime, sectorTimeLabel, sectorTone, sectorValue } from '../utils/sectorPresentation'
const FundCatalog = defineAsyncComponent(() => import('./FundCatalog.vue'))
const fundView = ref('ranking')
const base = import.meta.env.VITE_API_BASE_URL ?? '', api = createDataApi(base)
let requestUpdate = false
const { data, error, refreshing, stale, refresh } = useApiLoader(base + ':public:market', async signal => {
  const path = requestUpdate ? '/market/snapshot?fullCatalog=true&refresh=true' : '/market/snapshot?fullCatalog=true'
  requestUpdate = false
  const value = await api.read<MarketSnapshot>(path, signal)
  if (!Array.isArray(value.indices) || !Array.isArray(value.fund_rankings) || !Array.isArray(value.sector_rankings) || !Array.isArray(value.unavailable)) throw new Error('行情快照格式无效。')
  return value
}, 10000, false, 15000)
async function refreshMarket() { requestUpdate = true; await refresh(true) }
const account = useDashboardSummary(), route = useRoute()
const breadth = computed(() => data.value?.breadth ?? null)
const breadthTotal = computed(() => breadth.value ? breadth.value.advancing + breadth.value.declining : 0)
const breadthStats = computed(() => {
  const value = breadth.value, amount = value?.turnover == null ? null : Number(value.turnover)
  return [
    { label: '涨停家数', value: value ? number(value.limit_up, 0) : '—', title: '公开统计的涨停家数，包含一字板' },
    { label: '跌停家数', value: value ? number(value.limit_down, 0) : '—', title: '公开统计的跌停家数，包含一字板' },
    { label: '沪深成交额', value: amount == null ? '—' : amount >= 1e12 ? (amount / 1e12).toFixed(2) + ' 万亿' : (amount / 1e8).toFixed(2) + ' 亿', title: '人民币元；东方财富上证指数、深证成指口径成交额合计，与盘面统计日期一致，不含北交所' },
    { label: '较前日成交比', value: value?.turnover_ratio == null ? '—' : Number(value.turnover_ratio).toFixed(2) + ' 倍', title: value?.previous_trade_date ? `统计日成交额 / ${value.previous_trade_date} 全天成交额` : '统计日成交额 / 前一交易日全天成交额' },
  ]
})
const phaseLabel = (phase: string) => ({ pre_market: '盘前', auction: '集合竞价', continuous_trading: '连续交易', lunch_break: '午休', trading: '交易时段', break: '午休', closed: '休市', unknown: '状态待核验' })[phase] ?? '状态待核验'
const sessionLabel = computed(() => data.value?.market_sessions ? Object.entries({ CN: 'A股', HK: '港股', JP: '日本', US: '美股' }).map(([id, name]) => `${name} ${phaseLabel(data.value!.market_sessions![id] ?? 'unknown')}`).join(' / ') : 'A股 ' + phaseLabel(data.value?.market_session ?? 'unknown'))
const sourceLabel = (source: string) => ({ eastmoney: '东方财富', sina: '新浪财经', unknown: '原始快照' })[source as 'eastmoney' | 'sina' | 'unknown'] ?? source
const displayTime = (value?: string | null) => value ? value.slice(0, 19).replace('T', ' ') : '未取得'
const sectorSource = ref(''), sectorCategory = ref('行业'), sectorQuery = ref('')
const sectorKinds = [{ id: 'industry', label: '行业' }, { id: 'concept', label: '概念' }, { id: 'region', label: '地域' }]
const sectorSources = computed(() => data.value?.sector_catalogs?.length ? [...new Set(data.value.sector_catalogs.map(item => item.source))] : ['unknown'])
watch(data, value => { if (!sectorSource.value && value) sectorSource.value = value.sector_catalogs?.find(item => item.received > (item.unquoted ?? 0) && !item.stale)?.source ?? sectorSources.value[0] ?? 'unknown' }, { immediate: true })
const catalogFor = (category: string) => data.value?.sector_catalogs?.find(item => item.source === sectorSource.value && item.category === category)
const activeCatalog = computed(() => catalogFor(sectorKinds.find(item => item.label === sectorCategory.value)?.id ?? 'industry'))
const sectorStatus = computed(() => sectorCatalogStatus(activeCatalog.value, data.value?.market_session ?? 'unknown'))
const previewSource = computed(() => data.value?.sector_catalogs?.find(item => item.category === 'industry' && item.received > (item.unquoted ?? 0) && !item.stale)?.source ?? data.value?.sector_rankings[0]?.source ?? 'unknown')
const industryPreview = computed(() => (data.value?.sector_rankings ?? []).filter(item => (item.category ?? 'industry') === 'industry' && (item.source ?? 'unknown') === previewSource.value).sort((a, b) => (sectorValue(b) ?? -Infinity) - (sectorValue(a) ?? -Infinity)).slice(0, 6))
const sectorRows = computed(() => {
  const category = sectorKinds.find(item => item.label === sectorCategory.value)?.id ?? 'industry', query = sectorQuery.value.trim().toLowerCase()
  return (data.value?.sector_rankings ?? []).filter(item => (item.source ?? 'unknown') === sectorSource.value && (item.category ?? 'industry') === category && (item.name + item.code).toLowerCase().includes(query)).sort((a, b) => (sectorValue(b) ?? -Infinity) - (sectorValue(a) ?? -Infinity)).map(item => [item.name, item.code, sectorValue(item) == null ? '—' : percent(sectorValue(item)!), sectorAmount(item.turnover) == null || sectorValue(item) == null ? '—' : number(sectorAmount(item.turnover)! / 1e8, 2) + ' 亿', sectorValue(item) == null ? '—' : item.leader ?? '—', sectorTimeLabel(item), sectorQuality(item)])
})
const tabs = [{ id: 'index', label: '指数' }, { id: 'funds', label: '基金榜' }, { id: 'sectors', label: '板块' }]
const currentTab = ref(tabs.some(item => item.id === route.query.tab) ? String(route.query.tab) : 'index')
watch(() => route.query.tab, value => { if (tabs.some(item => item.id === value)) currentTab.value = String(value) })
const query = ref(''), category = ref('全部'), selected = ref<string>(), actionError = ref('')
const fundRows = computed(() => (data.value?.fund_rankings ?? []).filter(item => (item.name + item.symbol).includes(query.value.trim()) && (category.value === '全部' || (category.value === '上涨' ? Number(item.change_rate) >= 0 : Number(item.change_rate) < 0))).map(item => [item.name, item.symbol, item.unit_nav, item.change_rate + '%', item.nav_date ?? '—', item.symbol]))
const fundHighlights = computed(() => { const items = data.value?.fund_rankings ?? [], top = [...items].sort((a, b) => (b.change_rate == null ? -Infinity : Number(b.change_rate)) - (a.change_rate == null ? -Infinity : Number(a.change_rate)))[0]; return [{ title: top?.name ?? '暂无基金', value: top ? percent(Number(top.change_rate)) : '—' }, { title: top?.name ?? '暂无净值', value: top?.unit_nav ?? '—' }, { title: '可用基金', value: String(items.length) }, { title: '最近公告', value: top?.nav_date ?? '—' }] })
const isFollowed = (symbol: string) => account.data.value?.preferences.watchlist.some(item => item.symbol === symbol) ?? false
async function toggleFollow(symbol: string, name: string) { actionError.value = ''; try { const items = account.data.value?.preferences.watchlist ?? []; await account.mutate('/preferences/watchlist', { items: isFollowed(symbol) ? items.filter(item => item.symbol !== symbol) : [...items, { symbol, name, asset_type: 'fund' }] }, 'PUT') } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '关注操作失败。' } }
</script>

<style scoped>
.market-observation, .sector-source-note { margin: 10px 20px; color: var(--text-3); font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.market-observation { margin: 0 0 14px; }
.sector-source-note a { color: var(--accent); margin-left: 8px; }
.sector-source-label { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.sector-source-label select { width: auto; min-width: 120px; }
.metric-value small { font-size: 14px; }
@media (max-width: 640px) { .sector-data-table :deep(.sticky-name) { position: static; } }
@media (max-width: 600px) { .sector-source-label { flex-wrap: wrap; } .panel-head { flex-wrap: wrap; gap: 12px; } }

.market-page {
  width: 100%;
}

/* ── 顶部主标签页（完全对应图片风格） ── */
.market-nav-tabs {
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 2px 2px 14px;
  margin-bottom: 14px;
  border-bottom: 1px solid rgba(38, 59, 87, 0.45);
}

.market-tab-btn {
  background: transparent;
  border: none;
  font-size: 16px;
  font-weight: 600;
  color: #8da3bf;
  cursor: pointer;
  padding: 4px 2px 8px;
  position: relative;
  transition: all 0.22s cubic-bezier(0.4, 0, 0.2, 1);
  letter-spacing: 0.6px;
}

.market-tab-btn:hover {
  color: #e5eefc;
}

.market-tab-btn.active {
  color: #5896ff;
  text-shadow: 0 0 16px rgba(77, 141, 255, 0.45);
}

.market-tab-btn.active::after {
  content: "";
  position: absolute;
  bottom: -2px;
  left: 0;
  right: 0;
  height: 2.5px;
  background: linear-gradient(90deg, #4d8dff, #6ea8ff);
  border-radius: 2px;
  box-shadow: 0 0 10px rgba(77, 141, 255, 0.65);
}

/* 标签切换淡入动画 */
.tab-content-fade {
  animation: fadeIn 0.35s ease both;
}

/* ── 指数副条 ── */
.sub-indices-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 9px 16px;
  margin: 12px 0;
  background: rgba(16, 28, 47, 0.6);
  border: 1px solid rgba(38, 59, 87, 0.5);
  border-radius: 8px;
  overflow-x: auto;
}

.sub-index-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  white-space: nowrap;
}

.sub-index-name {
  color: var(--text-3);
}

.sub-index-val {
  font-weight: 600;
  color: #dce8fa;
}

.sub-index-delta {
  font-weight: 600;
}

/* ── 市场行情广度 ── */
.market-side-panels {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.market-industry-card {
  display: flex;
  flex: 1;
  flex-direction: column;
}

.market-industry-card .row-list,
.market-industry-card .row {
  flex: 1;
}

.market-breadth-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-header-inline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.text-link-btn {
  background: transparent;
  border: none;
  color: var(--accent);
  font-size: 11px;
  cursor: pointer;
  padding: 0;
  transition: color 0.2s;
}

.text-link-btn:hover {
  color: #79abff;
  text-decoration: underline;
}

.breadth-ratio-bar {
  display: flex;
  height: 24px;
  border-radius: 6px;
  overflow: hidden;
  font-size: 11px;
  font-weight: 600;
}

.breadth-fill {
  display: flex;
  align-items: center;
  justify-content: center;
  transition: width 0.6s ease;
  overflow: hidden;
  white-space: nowrap;
}

.breadth-empty {
  width: 100%;
  background: var(--surface);
}

.breadth-observation {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 4px 12px;
  margin: 0;
  font-size: 11px;
  color: var(--text-3);
}

.breadth-observation a {
  color: var(--text-3);
  text-decoration: none;
}

.breadth-fill.up {
  background: linear-gradient(90deg, #1f5a43, #2a835f);
  color: #7effd3;
}

.breadth-fill.down {
  background: linear-gradient(90deg, #7c2d3a, #541d26);
  color: #ff919d;
}

.breadth-stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  padding-top: 4px;
  border-top: 1px solid rgba(34, 51, 77, 0.5);
}

.breadth-stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
}

.industry-name {
  font-weight: 500;
}

/* ── 基金榜 KPI Cards ── */
.fund-kpi-grid,
.sector-kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.fund-kpi-card,
.sector-kpi-card {
  padding: 14px 16px;
  position: relative;
  overflow: hidden;
  animation: fadeInUp 0.4s ease both;
}

.fund-kpi-card.gold-border {
  border-color: rgba(255, 185, 56, 0.35);
  background: radial-gradient(350px 150px at 100% 0%, rgba(255, 185, 56, 0.08), transparent 70%), var(--card-bg);
}

.fund-kpi-card.blue-border {
  border-color: rgba(77, 141, 255, 0.35);
  background: radial-gradient(350px 150px at 100% 0%, rgba(77, 141, 255, 0.08), transparent 70%), var(--card-bg);
}

.fund-kpi-card.purple-border {
  border-color: rgba(157, 114, 255, 0.35);
  background: radial-gradient(350px 150px at 100% 0%, rgba(157, 114, 255, 0.08), transparent 70%), var(--card-bg);
}

.fund-kpi-card.green-border {
  border-color: rgba(53, 223, 176, 0.35);
  background: radial-gradient(350px 150px at 100% 0%, rgba(53, 223, 176, 0.08), transparent 70%), var(--card-bg);
}

.kpi-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.kpi-tag {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.kpi-tag.gold {
  background: rgba(255, 185, 56, 0.16);
  color: #ffc459;
}

.kpi-tag.blue {
  background: rgba(77, 141, 255, 0.16);
  color: #6ea8ff;
}

.kpi-tag.purple {
  background: rgba(157, 114, 255, 0.16);
  color: #bfa6ff;
}

.kpi-tag.green {
  background: rgba(53, 223, 176, 0.16);
  color: #61f2c7;
}

.kpi-main-name {
  font-size: 14px;
  font-weight: 600;
  color: #e6f0ff;
  margin: 6px 0 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kpi-metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(42, 64, 94, 0.4);
}

.kpi-metrics .subtle {
  font-size: 10px;
  color: var(--text-3);
}

.kpi-num {
  font-size: 16px;
  font-weight: 700;
  margin-top: 2px;
}

.kpi-sub-num {
  font-size: 13px;
  font-weight: 600;
  margin-top: 3px;
  color: #c9d8ec;
}

/* ── 周期筛选按钮组 ── */
.period-toggle-group {
  display: flex;
  background: rgba(19, 36, 60, 0.8);
  border: 1px solid rgba(42, 66, 97, 0.6);
  border-radius: 7px;
  padding: 2px;
}

.period-btn {
  border: none;
  background: transparent;
  color: #8da3bf;
  padding: 4px 10px;
  border-radius: 5px;
  font-size: 11px;
  transition: all 0.2s;
  cursor: pointer;
}

.period-btn:hover {
  color: #e5eefc;
}

.period-btn.active {
  background: linear-gradient(135deg, #2e6fe1, #4b8cff);
  color: #fff;
  box-shadow: 0 2px 8px rgba(46, 111, 225, 0.35);
}

/* ── 分类药丸按钮 ── */
.category-tabs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
}

.category-tabs button {
  border: 1px solid rgba(41, 62, 91, 0.7);
  background: rgba(19, 36, 59, 0.7);
  color: #9cafc8;
  padding: 6px 12px;
  border-radius: 7px;
  font-size: 12px;
  white-space: nowrap;
  transition: all 0.2s;
  cursor: pointer;
}

.category-tabs button:hover {
  color: var(--text-1);
  border-color: var(--accent);
}

.category-tabs button.active {
  background: rgba(77, 141, 255, 0.15);
  border-color: var(--accent);
  color: #79aeff;
  font-weight: 600;
}

/* ── 排名徽章 ── */
.rank-badge {
  display: inline-grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: rgba(30, 48, 73, 0.7);
  color: #8fa5c2;
  font-size: 11px;
  font-weight: 600;
}

.rank-badge.top1 {
  background: linear-gradient(135deg, #f7b731, #e18c12);
  color: #fff;
  box-shadow: 0 0 10px rgba(247, 183, 49, 0.5);
}

.rank-badge.top2 {
  background: linear-gradient(135deg, #a5b1c2, #778ca3);
  color: #fff;
  box-shadow: 0 0 8px rgba(165, 177, 194, 0.4);
}

.rank-badge.top3 {
  background: linear-gradient(135deg, #d38753, #aa6332);
  color: #fff;
  box-shadow: 0 0 8px rgba(211, 135, 83, 0.4);
}

.font-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.fund-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.micro-badge {
  font-size: 10px;
  background: rgba(77, 141, 255, 0.12);
  color: #6ea8ff;
  padding: 1px 5px;
  border-radius: 4px;
}

.type-badge {
  font-size: 10px;
  background: rgba(34, 54, 83, 0.6);
  color: #9cb1ce;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid rgba(50, 75, 110, 0.5);
}

.up-tag {
  color: var(--green);
  font-weight: 600;
}

.down-tag {
  color: var(--red);
  font-weight: 600;
}

.star-rating {
  color: #ffb938;
  letter-spacing: 1px;
  font-size: 12px;
}

.fav-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(22, 38, 62, 0.8);
  border: 1px solid rgba(43, 67, 99, 0.7);
  color: #8da3bf;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
}

.fav-btn:hover,
.fav-btn.saved {
  border-color: #ffb938;
  color: #ffc252;
}

/* ── 板块焦点磁贴 ── */
.hot-sectors-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.hot-sector-card {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: transform 0.25s, border-color 0.25s;
}

.hot-sector-card:hover {
  transform: translateY(-2px);
  border-color: var(--border-hover);
}

.sec-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sec-name {
  font-size: 14px;
  font-weight: 600;
  color: #e6f0ff;
}

.sec-stock-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
}

.sec-bar-wrap {
  height: 5px;
  background: rgba(30, 48, 73, 0.7);
  border-radius: 4px;
  overflow: hidden;
}

.sec-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease;
}

.sec-bar-fill.up-bar {
  background: linear-gradient(90deg, #2fb68f, #48e2b5);
  box-shadow: 0 0 6px rgba(47, 182, 143, 0.5);
}

.sec-bar-fill.down-bar {
  background: linear-gradient(90deg, #d34250, #ff6877);
  box-shadow: 0 0 6px rgba(211, 66, 80, 0.5);
}

.sec-foot {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
}

.sector-title-text {
  font-size: 13px;
  color: #e1edfc;
}

.leader-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.leader-name {
  color: #cddbec;
}

.stats-counter {
  margin-left: auto;
  font-size: 11px;
}

.spin-anim {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.impact-indicator {
  width: 90px;
  height: 6px;
  background: rgba(32, 52, 79, 0.7);
  border-radius: 3px;
  overflow: hidden;
}

.impact-indicator>div {
  height: 100%;
  background: linear-gradient(90deg, #3d80f0, #61a6ff);
  border-radius: 3px;
}
@media(max-width:1200px) {
  .fund-kpi-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .period-toggle-group { flex-wrap:wrap; }
  .market-page .panel-head { gap:12px; }
}
@media(max-width:640px) {
  .fund-kpi-grid,.sector-kpi-grid,.sectors-grid { grid-template-columns:1fr; }
  .kpi-main-name { font-size:13px; }
  .kpi-top { gap:6px; }
  .market-nav-tabs { gap:22px; }
  .period-toggle-group { width:100%; }
  .period-btn { flex:1 0 30%; min-height:32px; font-size:12px; }
}
</style>
